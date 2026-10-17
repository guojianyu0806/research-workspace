"""Durable PDF evidence storage for the first, non-LLM implementation increment."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from difflib import SequenceMatcher
import errno
import hashlib
import json
import math
import os
import re
import shutil
from pathlib import Path
import sqlite3
import threading
import time
from typing import Iterator
from uuid import uuid4

import pymupdf

from .structure import extract_structure


# PyMuPDF objects must never be used concurrently by API threads. The parser runs
# in a separate process; its lock therefore does not block API rendering.
MUPDF_LOCK = threading.RLock()
PARSER_VERSION = f"pymupdf-{pymupdf.VersionBind}/text-blocks-v4-supported-gutter"


class LibraryError(ValueError):
    """An invalid file or an invalid library operation."""


@contextmanager
def _schema_lock(database_path: Path, *, timeout: float = 30.0):
    """Serialize first-open/WAL/migrations across threads and worker processes.

    This is independent of the long-lived parsing worker lock. The OS releases
    the lock when the handle/process closes, so a crashed initializer cannot
    leave a stale ownership record that blocks later startup.
    """
    lock_path = database_path.with_name('.'+database_path.name+'.schema.lock')
    with lock_path.open('a+b') as handle:
        deadline = time.monotonic()+timeout
        while True:
            handle.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(),fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES,errno.EAGAIN,errno.EDEADLK}:
                    raise
                if time.monotonic() >= deadline:
                    raise LibraryError('工作空间正在初始化，请稍后重试。') from None
                time.sleep(.025)
        try:
            # Windows permits locking a range beyond EOF. Initialize the byte
            # only after ownership: a pre-lock buffered flush can otherwise
            # collide with another initializer that already locked byte zero.
            if lock_path.stat().st_size == 0:
                handle.write(b'\0')
                handle.flush()
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:
                fcntl.flock(handle.fileno(),fcntl.LOCK_UN)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())


class Library:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir).resolve()
        self.files_dir = self.data_dir / "originals"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.database_path = self.data_dir / "library.sqlite3"
        with _schema_lock(self.database_path), self._connection() as db:
            if db.execute('PRAGMA journal_mode').fetchone()[0].lower() != 'wal':
                db.execute("PRAGMA journal_mode=WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, filename TEXT NOT NULL,
                    title TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE,
                    storage_name TEXT NOT NULL UNIQUE, page_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL, current_run_id TEXT
                );
                CREATE TABLE IF NOT EXISTS parse_runs (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
                    version INTEGER NOT NULL, parser_version TEXT NOT NULL,
                    status TEXT NOT NULL, pages_done INTEGER NOT NULL DEFAULT 0,
                    total_pages INTEGER NOT NULL, text_pages INTEGER NOT NULL DEFAULT 0,
                    no_text_pages INTEGER NOT NULL DEFAULT 0,
                    failed_pages INTEGER NOT NULL DEFAULT 0, error TEXT,
                    created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT,
                    UNIQUE(document_id, version)
                );
                CREATE TABLE IF NOT EXISTS pages (
                    run_id TEXT NOT NULL REFERENCES parse_runs(id),
                    page INTEGER NOT NULL, width REAL NOT NULL, height REAL NOT NULL,
                    rotation INTEGER NOT NULL, status TEXT NOT NULL,
                    text TEXT NOT NULL, error TEXT,
                    PRIMARY KEY(run_id, page)
                );
                CREATE TABLE IF NOT EXISTS blocks (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, page INTEGER NOT NULL,
                    ordinal INTEGER NOT NULL, text TEXT NOT NULL,
                    search_text TEXT NOT NULL, bbox TEXT NOT NULL,
                    FOREIGN KEY(run_id, page) REFERENCES pages(run_id, page),
                    UNIQUE(run_id, page, ordinal)
                );
                CREATE INDEX IF NOT EXISTS runs_status ON parse_runs(status, created_at);
                CREATE INDEX IF NOT EXISTS blocks_run_page ON blocks(run_id, page);
                CREATE TABLE IF NOT EXISTS version_families (
                    id TEXT PRIMARY KEY, current_document_id TEXT, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS version_relations (
                    id TEXT PRIMARY KEY, family_id TEXT NOT NULL,
                    document_id TEXT NOT NULL, other_id TEXT NOT NULL,
                    action TEXT NOT NULL, created_at TEXT NOT NULL
                );
                """
            )
            db.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in db.execute("PRAGMA table_info(documents)")}
            for name, definition in {
                "authors": "TEXT NOT NULL DEFAULT '[]'",
                "archived_at": "TEXT",
                "version_family_id": "TEXT",
            }.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE documents ADD COLUMN {name} {definition}")
            for table, additions in {
                "parse_runs": {"cancel_requested": "INTEGER NOT NULL DEFAULT 0",
                               "parent_run_id": "TEXT", "target_pages": "TEXT",
                               "cursor_page": "INTEGER NOT NULL DEFAULT 0", "scheduled_at": "TEXT"},
                "pages": {"extraction_method": "TEXT NOT NULL DEFAULT 'native'", "notice": "TEXT"},
            }.items():
                existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
                for name, definition in additions.items():
                    if name not in existing:
                        db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=30000")
        try:
            yield db
            if db.in_transaction:
                db.commit()
        except BaseException:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _run(db: sqlite3.Connection, run_id: str) -> dict:
        row = db.execute("SELECT * FROM parse_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        result = dict(row)
        result["cancel_requested"] = bool(result["cancel_requested"])
        result["target_pages"] = json.loads(result["target_pages"]) if result["target_pages"] else None
        return result

    def _document(self, db: sqlite3.Connection, document_id: str) -> dict:
        row = db.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        if row is None:
            raise KeyError(document_id)
        result = dict(row)
        result["authors"] = json.loads(result["authors"])
        family = db.execute("SELECT current_document_id FROM version_families WHERE id=?",
                            (result["version_family_id"],)).fetchone()
        result["is_current_version"] = not family or family[0] is None or family[0] == document_id
        result.pop("storage_name")
        current_id = result.pop("current_run_id")
        result["current_run"] = self._run(db, current_id) if current_id else None
        available = db.execute(
            """SELECT id FROM parse_runs WHERE document_id=?
               AND status IN ('completed','partial') AND text_pages>0
               ORDER BY version DESC LIMIT 1""", (document_id,),
        ).fetchone()
        # The newest task describes processing progress; a failed/in-flight
        # replacement must not hide the last finished, readable evidence set.
        result["available_run"] = self._run(db, available[0]) if available else None
        return result

    @staticmethod
    def _new_run(db: sqlite3.Connection, document_id: str, page_count: int) -> str:
        run_id = uuid4().hex
        version = db.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM parse_runs WHERE document_id=?",
            (document_id,),
        ).fetchone()[0]
        db.execute(
            """INSERT INTO parse_runs
               (id,document_id,version,parser_version,status,total_pages,created_at)
               VALUES (?,?,?,?,'queued',?,?)""",
            (run_id, document_id, version, PARSER_VERSION, page_count, _now()),
        )
        db.execute("UPDATE documents SET current_run_id=? WHERE id=?", (run_id, document_id))
        return run_id

    def import_document(self, content: bytes, filename: str) -> dict:
        if not content:
            raise LibraryError("PDF文件为空。")
        digest = hashlib.sha256(content).hexdigest()
        with self._connection() as db:
            duplicate = db.execute("SELECT id FROM documents WHERE sha256=?", (digest,)).fetchone()
            if duplicate:
                return {"document": self._document(db, duplicate[0]), "duplicate": True}
        filename = Path(filename.replace("\\", "/")).name.strip()[:255] or "document.pdf"
        try:
            with MUPDF_LOCK, pymupdf.open(stream=content, filetype="pdf") as pdf:
                if not pdf.is_pdf:
                    raise LibraryError("文件不是有效的PDF。")
                if pdf.needs_pass or pdf.is_encrypted or (pdf.metadata or {}).get("encryption"):
                    raise LibraryError("暂不支持加密或需要密码的PDF，请先提供可直接读取的文件。")
                page_count = pdf.page_count
                if page_count < 1:
                    raise LibraryError("PDF没有可读取的页面。")
                title = ((pdf.metadata or {}).get("title") or "").strip() or Path(filename).stem
                # Accessing the first page detects several damaged page-tree cases.
                pdf.load_page(0)
        except LibraryError:
            raise
        except Exception as exc:
            raise LibraryError("无法读取PDF，文件可能已损坏或格式不正确。") from exc

        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            duplicate = db.execute("SELECT id FROM documents WHERE sha256=?", (digest,)).fetchone()
            if duplicate:
                return {"document": self._document(db, duplicate[0]), "duplicate": True}
            document_id = uuid4().hex
            storage_name = f"{document_id}.pdf"
            # Exclusive creation: neither a prior upload nor a retained original
            # can be overwritten. A crash can leave an orphan, never a fake record.
            with (self.files_dir / storage_name).open("xb") as output:
                output.write(content)
            db.execute(
                """INSERT INTO documents
                   (id,filename,title,sha256,storage_name,page_count,created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (document_id, filename, title, digest, storage_name, page_count, _now()),
            )
            self._new_run(db, document_id, page_count)
            return {"document": self._document(db, document_id), "duplicate": False}

    def list_documents(self, include_archived: bool = False) -> list[dict]:
        with self._connection() as db:
            clause = "" if include_archived else " WHERE archived_at IS NULL"
            ids = db.execute("SELECT id FROM documents" + clause + " ORDER BY created_at DESC").fetchall()
            return [self._document(db, row[0]) for row in ids]

    def update_metadata(self, document_id: str, *, title: str | None = None,
                        authors: list[str] | None = None) -> dict:
        if title is not None and (not isinstance(title, str) or not title.strip() or len(title) > 1000):
            raise LibraryError("标题必须为1至1000个字符。")
        if authors is not None and (not isinstance(authors, list) or len(authors) > 200 or
                any(not isinstance(name, str) or not name.strip() or len(name) > 200 for name in authors)):
            raise LibraryError("作者必须为非空姓名列表，每个姓名最多200个字符。")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._document(db, document_id)
            if title is not None:
                db.execute("UPDATE documents SET title=? WHERE id=?", (title.strip(), document_id))
            if authors is not None:
                db.execute("UPDATE documents SET authors=? WHERE id=?",
                           (json.dumps([name.strip() for name in authors], ensure_ascii=False), document_id))
            return self._document(db, document_id)

    def archive_document(self, document_id: str) -> dict:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._document(db, document_id)
            db.execute("UPDATE documents SET archived_at=COALESCE(archived_at,?) WHERE id=?", (_now(), document_id))
            return self._document(db, document_id)

    def restore_document(self, document_id: str) -> dict:
        with self._connection() as db:
            self._document(db, document_id)
            db.execute("UPDATE documents SET archived_at=NULL WHERE id=?", (document_id,))
            return self._document(db, document_id)

    def _evidence_blocks(self, document_id: str, run_id: str | None = None) -> list[dict]:
        with self._connection() as db:
            doc = self._document(db, document_id)
            run = self._run(db, run_id) if run_id else doc["available_run"] or doc["current_run"]
            if run["document_id"] != document_id:
                raise LibraryError("解析版本不属于指定文档。")
            return [dict(row) | {"document_id": document_id, "bbox": json.loads(row["bbox"])}
                    for row in db.execute(
                        "SELECT id AS block_id,run_id,page,text,bbox FROM blocks WHERE run_id=? ORDER BY page,ordinal",
                        (run["id"],),
                    )]

    def _version_signature(self, document_id: str) -> dict:
        doc = self.get_document(document_id)
        blocks = self._evidence_blocks(document_id)
        body = _normalize(" ".join(block["text"] for block in blocks))
        identity = None
        arxiv_pattern = r"(\d{4}\.\d{4,5}|[a-z][a-z.\-]+/\d{7})(?:v(\d+))?"
        # Filename ids are only accepted as the complete stem; body ids require
        # the explicit arXiv label, avoiding ordinary numeric text matches.
        match = re.fullmatch(arxiv_pattern, Path(doc["filename"]).stem, re.I)
        source = "filename"
        if not match:
            match = re.search(r"\barxiv\s*:\s*" + arxiv_pattern, body[:12000], re.I)
            source = "first_pages_text"
        if match:
            identity = {"id": match[1].lower(), "version": int(match[2]) if match[2] else None,
                        "source": source}
        tokens = re.findall(r"\w+", body)
        # Five-word shingle overlap is a content fingerprint, not an assertion
        # that thematic similarity establishes paper identity.
        shingles = {hashlib.blake2b(" ".join(tokens[i:i + 5]).encode(), digest_size=8).hexdigest()
                    for i in range(max(0, len(tokens) - 4))}
        return {"document": doc, "arxiv": identity, "shingles": shingles,
                "body_preview": body[:1600],
                "token_count": len(tokens), "body_sha256": hashlib.sha256(body.encode()).hexdigest() if body else None}

    def find_version_candidates(self, document_id: str) -> list[dict]:
        source = self._version_signature(document_id)
        candidates = []
        others=[self._version_signature(d['id']) for d in self.list_documents() if d['id']!=document_id]
        try:
            from .semantic import document_similarities
            semantic_scores=document_similarities(self,[source,*others])
        except (ImportError,OSError,ValueError,RuntimeError):
            semantic_scores={}
        for other in others:
            document=other['document']
            same_id = bool(source["arxiv"] and other["arxiv"] and source["arxiv"]["id"] == other["arxiv"]["id"])
            union = source["shingles"] | other["shingles"]
            similarity = len(source["shingles"] & other["shingles"]) / len(union) if union else 0
            exact_body = bool(source["body_sha256"] and source["body_sha256"] == other["body_sha256"])
            enough_body = min(source["token_count"], other["token_count"]) >= 60
            semantic_score=semantic_scores.get(document['id'])
            semantic_candidate=(semantic_score is not None and semantic_score>=.95 and similarity>=.25)
            if not (same_id or (enough_body and (similarity >= 0.8 or exact_body or semantic_candidate))):
                continue
            conflicting_ids = bool(source["arxiv"] and other["arxiv"] and not same_id)
            candidates.append({"document": document, "arxiv": other["arxiv"],
                               "source_arxiv": source["arxiv"], "body_similarity": round(similarity, 4),
                               "body_fingerprint_equal": exact_body, "conflicting_identifiers": conflicting_ids,
                               "semantic_similarity":round(semantic_score,5) if semantic_score is not None else None,
                               "semantic_scope":"first_1600_normalized_body_chars; advisory_only" if semantic_score is not None else None,
                               "reason": "same_arxiv_id" if same_id else "high_body_overlap" if similarity>=.8 or exact_body else "semantic_and_body_overlap",
                               "requires_confirmation": True,
                               "limitations": "正文重合和标识均为候选依据，不能自动证明同一论文。"})
        return sorted(candidates, key=lambda item: (item["reason"] == "same_arxiv_id", item["body_similarity"]), reverse=True)

    def confirm_version_relation(self, document_id: str, other_id: str, *, action: str = "keep_both") -> dict:
        if document_id == other_id or action not in {"keep_both", "use_document", "use_other"}:
            raise LibraryError("请选择不同文档及有效的版本保留方式。")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            left, right = self._document(db, document_id), self._document(db, other_id)
            selected = document_id if action == "use_document" else other_id if action == "use_other" else None
            if selected and (left if selected == document_id else right)["archived_at"]:
                raise LibraryError("请先恢复归档文档，再将其设为当前版本。")
            family_id = left["version_family_id"] or right["version_family_id"] or uuid4().hex
            db.execute("INSERT OR IGNORE INTO version_families(id,created_at) VALUES (?,?)", (family_id, _now()))
            for document in (left, right):
                previous_family = document["version_family_id"]
                if previous_family and previous_family != family_id:
                    db.execute("UPDATE documents SET version_family_id=? WHERE version_family_id=?", (family_id, previous_family))
                    db.execute("UPDATE version_relations SET family_id=? WHERE family_id=?", (family_id, previous_family))
                db.execute("UPDATE documents SET version_family_id=? WHERE id=?", (family_id, document["id"]))
            if selected:
                db.execute("UPDATE version_families SET current_document_id=? WHERE id=?", (selected, family_id))
            relation = {"id": uuid4().hex, "family_id": family_id, "document_id": document_id,
                        "other_id": other_id, "action": action, "created_at": _now()}
            db.execute("INSERT INTO version_relations(id,family_id,document_id,other_id,action,created_at) VALUES (?,?,?,?,?,?)",
                       tuple(relation.values()))
            return relation

    def get_version_family(self, document_id: str) -> dict:
        with self._connection() as db:
            doc = self._document(db, document_id)
            family_id = doc["version_family_id"]
            if not family_id:
                return {"family_id": None, "current_document_id": document_id, "documents": [doc], "relations": []}
            current = db.execute("SELECT current_document_id FROM version_families WHERE id=?", (family_id,)).fetchone()[0]
            ids = db.execute("SELECT id FROM documents WHERE version_family_id=? ORDER BY created_at", (family_id,)).fetchall()
            relations = [dict(row) for row in db.execute("SELECT * FROM version_relations WHERE family_id=? ORDER BY created_at", (family_id,))]
            return {"family_id": family_id, "current_document_id": current,
                    "documents": [self._document(db, row[0]) for row in ids], "relations": relations}

    def compare_versions(self, document_id: str, other_id: str) -> dict:
        before, after = self._evidence_blocks(document_id), self._evidence_blocks(other_id)
        if not before or not after:
            raise LibraryError("两个版本都需要有可提取正文后才能比较。")
        matcher = SequenceMatcher(a=[_normalize(block["text"]) for block in before],
                                  b=[_normalize(block["text"]) for block in after], autojunk=False)
        changes = []
        for kind, i, j, x, y in matcher.get_opcodes():
            if kind == "equal":
                continue
            for offset in range(max(j - i, y - x)):
                changes.append({"kind": kind, "before": before[i + offset] if i + offset < j else None,
                                "after": after[x + offset] if x + offset < y else None})
        return {"document_id": document_id, "other_id": other_id,
                "method": "normalized_text_block_diff", "changes": changes,
                "limitations": ["这是正文块差异，不是语义修订判定。", "换栏、断行和分块变化仍可能形成差异，需核对两侧原文。"]}

    def get_document(self, document_id: str) -> dict:
        with self._connection() as db:
            return self._document(db, document_id)

    def get_run(self, run_id: str) -> dict:
        with self._connection() as db:
            return self._run(db, run_id)

    def get_structure(self, document_id: str, run_id: str | None = None) -> dict:
        document = self.get_document(document_id)
        run_id = run_id or (document["available_run"] or document["current_run"])["id"]
        result = extract_structure(self._evidence_blocks(document_id, run_id), document_id, run_id)
        result["processing_status"] = self.get_run(run_id)["status"]
        return result

    def list_runs(self, document_id: str) -> list[dict]:
        with self._connection() as db:
            self._document(db, document_id)
            return [self._run(db, row[0]) for row in db.execute(
                "SELECT id FROM parse_runs WHERE document_id=? ORDER BY version DESC", (document_id,)
            )]

    def cancel_run(self, run_id: str) -> dict:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            run = self._run(db, run_id)
            if run["status"] == "queued":
                db.execute("UPDATE parse_runs SET status='cancelled',cancel_requested=1,completed_at=? WHERE id=?",
                           (_now(), run_id))
            elif run["status"] == "running":
                db.execute("UPDATE parse_runs SET cancel_requested=1 WHERE id=?", (run_id,))
            return self._run(db, run_id)

    def retry_page(self, document_id: str, page: int, run_id: str | None = None) -> dict:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            document = self._document(db, document_id)
            self._validate_page(page, document["page_count"])
            source_id = run_id or (document["available_run"] or document["current_run"])["id"]
            source = self._run(db, source_id)
            if source["document_id"] != document_id:
                raise LibraryError("解析版本不属于指定文档。")
            if db.execute("SELECT 1 FROM parse_runs WHERE document_id=? AND status IN ('queued','running')",
                          (document_id,)).fetchone():
                raise LibraryError("请等待或取消当前任务后再创建单页重试。")
            new_id = self._new_run(db, document_id, document["page_count"])
            db.execute("UPDATE parse_runs SET parent_run_id=?,target_pages=? WHERE id=?",
                       (source_id, json.dumps([page]), new_id))
            # Copy-on-write: retained pages get new evidence ids in the new run,
            # while all source page/block records remain unchanged.
            db.execute("""INSERT INTO pages(run_id,page,width,height,rotation,status,text,error,extraction_method,notice)
                          SELECT ?,page,width,height,rotation,status,text,error,extraction_method,notice
                          FROM pages WHERE run_id=? AND page!=?""", (new_id, source_id, page))
            old_blocks = db.execute("SELECT * FROM blocks WHERE run_id=? AND page!=?", (source_id, page)).fetchall()
            db.executemany("INSERT INTO blocks(id,run_id,page,ordinal,text,search_text,bbox) VALUES (?,?,?,?,?,?,?)",
                           [(uuid4().hex, new_id, item["page"], item["ordinal"], item["text"], item["search_text"], item["bbox"])
                            for item in old_blocks])
            self._update_counts(db, new_id)
            return self._run(db, new_id)

    @staticmethod
    def _ocr_configuration() -> dict:
        enabled = os.getenv("RESEARCH_OCR_ENABLED", "auto").lower() not in {"0", "false", "off", "no"}
        language = os.getenv("RESEARCH_OCR_LANG", "eng")
        explicit = os.getenv("RESEARCH_OCR_TESSDATA") or os.getenv("TESSDATA_PREFIX")
        result = {"enabled": enabled, "available": False, "configured": bool(explicit),
                  "languages": language, "backend": "pymupdf_tesseract", "path": None}
        if not enabled:
            return result | {"reason": "OCR已在运行配置中关闭。"}
        if not re.fullmatch(r"[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*", language):
            return result | {"reason": "OCR语言配置无效。"}
        executable = shutil.which("tesseract")
        candidates = [Path(explicit)] if explicit else []
        if not explicit:
            if executable:
                candidates.append(Path(executable).parent / "tessdata")
            candidates.extend([Path("C:/Program Files/Tesseract-OCR/tessdata"),
                               Path("/usr/share/tesseract-ocr/5/tessdata"), Path("/usr/share/tessdata")])
        for candidate in candidates:
            if candidate.is_dir() and all((candidate / f"{name}.traineddata").is_file() for name in language.split("+")):
                path = str(candidate.resolve())
                # Tesseract's Windows C file APIs may reject non-ASCII absolute
                # paths. A relative ASCII path resolves to the same existing data
                # without moving files or changing the process working directory.
                if os.name == 'nt' and not path.isascii():
                    try:
                        relative = os.path.relpath(candidate.resolve(), Path.cwd())
                        if relative.isascii():
                            path = relative
                    except ValueError:
                        pass
                return result | {"available": True, "configured": True, "path": path,
                                 "reason": "语言数据可读取，实际OCR效果以逐页任务结果为准。"}
        return result | {"reason": "未找到所需OCR语言数据；请配置已有的Tesseract语言数据目录。未安装或下载任何组件。"}

    def ocr_status(self) -> dict:
        settings = self._ocr_configuration()
        settings.pop("path")
        return settings

    def create_run(self, document_id: str) -> dict:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            document = self._document(db, document_id)
            active = db.execute(
                "SELECT id FROM parse_runs WHERE document_id=? AND status IN ('queued','running')",
                (document_id,),
            ).fetchone()
            if active:
                return self._run(db, active[0])
            return self._run(db, self._new_run(db, document_id, document["page_count"]))

    def _source_path(self, document_id: str) -> Path:
        with self._connection() as db:
            row = db.execute("SELECT storage_name FROM documents WHERE id=?", (document_id,)).fetchone()
            if row is None:
                raise KeyError(document_id)
            return self.files_dir / row[0]

    @staticmethod
    def _validate_page(page: int, page_count: int) -> None:
        if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= page_count:
            raise LibraryError(f"页码必须介于1和{page_count}之间。")

    def get_page(self, document_id: str, page: int, run_id: str | None = None) -> dict:
        with self._connection() as db:
            document = self._document(db, document_id)
            self._validate_page(page, document["page_count"])
            default_run = document["available_run"] or document["current_run"]
            run_id = run_id or default_run["id"]
            run = self._run(db, run_id)
            if run["document_id"] != document_id:
                raise LibraryError("解析版本不属于指定文档。")
            row = db.execute("SELECT * FROM pages WHERE run_id=? AND page=?", (run_id, page)).fetchone()
            if row:
                result = dict(row)
                result["blocks"] = [
                    {"id": b["id"], "text": b["text"], "bbox": json.loads(b["bbox"]), "page": page}
                    for b in db.execute(
                        "SELECT * FROM blocks WHERE run_id=? AND page=? ORDER BY ordinal", (run_id, page)
                    )
                ]
            else:
                result = {"run_id": run_id, "page": page, "status": "pending", "blocks": [],
                          "text": "", "error": None, "extraction_method": None, "notice": None}
        if row is None:
            with MUPDF_LOCK, pymupdf.open(self._source_path(document_id)) as pdf:
                source_page = pdf.load_page(page - 1)
                result.update(width=source_page.rect.width, height=source_page.rect.height,
                              rotation=source_page.rotation)
        result["document_id"] = document_id
        result["coordinate_system"] = "normalized_rendered_page"
        return result

    def render_page(self, document_id: str, page: int, scale: float = 1.25) -> bytes:
        document = self.get_document(document_id)
        self._validate_page(page, document["page_count"])
        if not math.isfinite(scale) or not 0.25 <= scale <= 3:
            raise LibraryError("页面渲染倍率必须介于0.25和3之间。")
        with MUPDF_LOCK, pymupdf.open(self._source_path(document_id)) as pdf:
            source_page = pdf.load_page(page - 1)
            if source_page.rect.width * source_page.rect.height * scale * scale > 16_000_000:
                raise LibraryError("请求的页面图片过大，请降低渲染倍率。")
            return source_page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("png")

    def search(self, query: str, document_id: str | None = None,
               run_id: str | None = None, limit: int = 30) -> list[dict]:
        normalized = _normalize(query)
        if not normalized:
            return []
        if len(normalized) > 500:
            raise LibraryError("检索词不能超过500个字符。")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise LibraryError("结果条数必须介于1和100之间。")
        with self._connection() as db:
            if document_id:
                self._document(db, document_id)
            if run_id:
                run = self._run(db, run_id)
                if document_id and run["document_id"] != document_id:
                    raise LibraryError("解析版本不属于指定文档。")
            filters = ["instr(b.search_text, ?) > 0"]
            parameters: list = [normalized]
            if document_id:
                filters.append("d.id=?")
                parameters.append(document_id)
            elif not run_id:
                filters.append("d.archived_at IS NULL")
                filters.append("""NOT EXISTS (SELECT 1 FROM version_families vf
                    WHERE vf.id=d.version_family_id AND vf.current_document_id IS NOT NULL
                    AND vf.current_document_id!=d.id)""")
            if run_id:
                filters.append("r.id=?")
                parameters.append(run_id)
            else:
                # Select exactly one effective run for each document. Explicit
                # run_id queries remain able to inspect any retained revision.
                filters.append(
                    """r.id=COALESCE(
                        (SELECT available.id FROM parse_runs available
                         WHERE available.document_id=d.id
                         AND available.status IN ('completed','partial')
                         AND available.text_pages>0
                         ORDER BY available.version DESC LIMIT 1),
                        d.current_run_id)"""
                )
            parameters.append(limit)
            rows = db.execute(
                """SELECT d.id AS document_id,d.title AS document_title,d.filename,
                          r.id AS run_id,b.page,b.id AS block_id,b.text,b.bbox
                   FROM blocks b JOIN parse_runs r ON b.run_id=r.id
                   JOIN documents d ON r.document_id=d.id WHERE """
                + " AND ".join(filters)
                + " ORDER BY d.created_at DESC,b.page,b.ordinal LIMIT ?", parameters,
            ).fetchall()
            return [{**dict(row), "bbox": json.loads(row["bbox"])} for row in rows]

    def recover_interrupted_runs(self) -> int:
        """Only the OS-lock-owning worker may call this startup recovery method."""
        with self._connection() as db:
            db.execute("UPDATE parse_runs SET status='cancelled',completed_at=? WHERE status='running' AND cancel_requested=1", (_now(),))
            cursor = db.execute(
                "UPDATE parse_runs SET status='queued', completed_at=NULL,cursor_page=0,scheduled_at=? WHERE status='running' AND cancel_requested=0",
                (_now(),),
            )
            return cursor.rowcount

    def _claim_run(self) -> dict | None:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id FROM parse_runs WHERE status='queued' AND cancel_requested=0 ORDER BY COALESCE(scheduled_at,created_at),created_at LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE parse_runs SET status='running',started_at=COALESCE(started_at,?),error=NULL WHERE id=?", (_now(), row[0]))
            return self._run(db, row[0])

    @staticmethod
    def _page_blocks(source_page: pymupdf.Page, textpage=None) -> list[dict]:
        width, height = source_page.rect.width, source_page.rect.height
        result = []
        unrotated_boxes = {}
        for raw in source_page.get_text("blocks", sort=True, textpage=textpage):
            if raw[6] != 0 or not raw[4].strip():
                continue
            # get_text coordinates are unrotated and crop-relative. Applying the
            # page rotation yields the exact coordinate system used by get_pixmap.
            rect = (pymupdf.Rect(raw[:4]) * source_page.rotation_matrix) & source_page.rect
            if rect.is_empty:
                continue
            bbox = [max(0.0, min(1.0, value)) for value in
                    (rect.x0 / width, rect.y0 / height, rect.x1 / width, rect.y1 / height)]
            item={"id": uuid4().hex, "text": raw[4].strip(), "bbox": bbox}
            result.append(item)
            unrotated_boxes[item["id"]]=raw[:4]
        # Detect columns in the PDF text coordinate system, before display
        # rotation. Only ordinal changes; quotes and rendered bbox stay intact.
        from .reading_order import reading_order
        order=reading_order(result,unrotated_boxes,source_page.cropbox.width)
        by_id={item["id"]:item for item in result}
        return [by_id[identifier] for identifier in order["region_ids"]]

    def _save_page(self, run_id: str, page: int, width: float, height: float,
                   rotation: int, blocks: list[dict], error: str | None = None,
                   *, extraction_method: str = "native", notice: str | None = None) -> None:
        status = "failed" if error else ("parsed" if blocks else "no_extractable_text")
        text = "\n\n".join(block["text"] for block in blocks)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            # Recovery only reprocesses absent or failed pages. Successful page
            # evidence is immutable, including block ids already returned to users.
            existing = db.execute("SELECT status FROM pages WHERE run_id=? AND page=?", (run_id, page)).fetchone()
            if existing and existing[0] != "failed":
                return
            db.execute("DELETE FROM blocks WHERE run_id=? AND page=?", (run_id, page))
            db.execute(
                """INSERT INTO pages(run_id,page,width,height,rotation,status,text,error,extraction_method,notice)
                   VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id,page) DO UPDATE SET
                   width=excluded.width,height=excluded.height,rotation=excluded.rotation,
                   status=excluded.status,text=excluded.text,error=excluded.error,
                   extraction_method=excluded.extraction_method,notice=excluded.notice""",
                (run_id, page, width, height, rotation, status, text, error, extraction_method, notice),
            )
            db.executemany(
                "INSERT INTO blocks(id,run_id,page,ordinal,text,search_text,bbox) VALUES (?,?,?,?,?,?,?)",
                [(b["id"], run_id, page, index, b["text"], _normalize(b["text"]), json.dumps(b["bbox"]))
                 for index, b in enumerate(blocks)],
            )
            self._update_counts(db, run_id)
            # Attempted failures count toward the current pass as well. Otherwise
            # a one-page budget would keep retrying the first failed page forever.
            db.execute('UPDATE parse_runs SET cursor_page=MAX(cursor_page,?) WHERE id=?',(page,run_id))

    @staticmethod
    def _update_counts(db: sqlite3.Connection, run_id: str) -> None:
        counts = dict(db.execute("SELECT status,COUNT(*) FROM pages WHERE run_id=? GROUP BY status", (run_id,)))
        db.execute(
            "UPDATE parse_runs SET pages_done=?,text_pages=?,no_text_pages=?,failed_pages=? WHERE id=?",
            (sum(counts.values()), counts.get("parsed", 0), counts.get("no_extractable_text", 0),
             counts.get("failed", 0), run_id),
        )

    def _finish_cancellation(self, run_id: str) -> bool:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            run = self._run(db, run_id)
            if not run["cancel_requested"]:
                return False
            db.execute("UPDATE parse_runs SET status='cancelled',completed_at=? WHERE id=?", (_now(), run_id))
            return True

    def process_next_job(self, *, page_budget: int | None = None) -> bool:
        """Process a full parse by default, or persist at most page_budget pages.

        Budgeted calls requeue unfinished work without changing saved evidence.
        Startup recovery resets the pass cursor and may retry failed pages, while
        already successful page/block records stay immutable.
        """
        if page_budget is not None and (isinstance(page_budget,bool) or not isinstance(page_budget,int) or page_budget<1):
            raise LibraryError('解析页预算必须为正整数。')
        run = self._claim_run()
        if run is None:
            return False
        run_id = run["id"]
        try:
            ocr = self._ocr_configuration()
            with self._connection() as db:
                done = {row[0] for row in db.execute(
                    "SELECT page FROM pages WHERE run_id=? AND status!='failed'", (run_id,)
                )}
            pending = [page for page in range(1,run['total_pages']+1)
                       if page not in done and page>run['cursor_page']
                       and (run['target_pages'] is None or page in run['target_pages'])]
            selected = pending if page_budget is None else pending[:page_budget]
            with MUPDF_LOCK, pymupdf.open(self._source_path(run["document_id"])) as pdf:
                for page_number in selected:
                    if self._finish_cancellation(run_id):
                        return True
                    width = height = 0.0
                    rotation = 0
                    extraction_method = "native"
                    try:
                        source_page = pdf.load_page(page_number - 1)
                        width, height, rotation = source_page.rect.width, source_page.rect.height, source_page.rotation
                        blocks = self._page_blocks(source_page)
                        notice = None
                        if not blocks and ocr["available"]:
                            extraction_method = "ocr"
                            if width * height * 4 > 16_000_000:
                                raise LibraryError("页面过大，OCR渲染超过资源限制。")
                            textpage = source_page.get_textpage_ocr(
                                language=ocr["languages"], dpi=144, full=True, tessdata=ocr["path"],
                            )
                            blocks = self._page_blocks(source_page, textpage=textpage)
                            notice = "本页文字由OCR识别，请核对原页；公式和复杂版式仍可能不准确。"
                        elif not blocks:
                            notice = ocr["reason"]
                        self._save_page(run_id, page_number, width, height, rotation, blocks,
                                        extraction_method=extraction_method, notice=notice)
                    except Exception as exc:
                        error = (f"第{page_number}页OCR失败（{type(exc).__name__}），请检查语言数据与OCR配置。"
                                 if extraction_method == "ocr" else
                                 f"第{page_number}页解析失败：{type(exc).__name__}: {exc}"[:1000])
                        self._save_page(run_id, page_number, width, height, rotation, [],
                                        error=error,
                                        extraction_method=extraction_method)
            if len(selected)<len(pending):
                with self._connection() as db:
                    db.execute('BEGIN IMMEDIATE')
                    current = self._run(db,run_id)
                    cancelled = current['cancel_requested']
                    db.execute('UPDATE parse_runs SET status=?,scheduled_at=?,completed_at=? WHERE id=?',
                               ('cancelled' if cancelled else 'queued',_now(),_now() if cancelled else None,run_id))
                return True
            with self._connection() as db:
                db.execute("BEGIN IMMEDIATE")
                self._update_counts(db, run_id)
                current = self._run(db, run_id)
                if current["cancel_requested"]:
                    status = "cancelled"
                elif current["pages_done"] < current["total_pages"]:
                    status = "partial"
                elif current["failed_pages"] == current["total_pages"]:
                    status = "failed"
                elif current["no_text_pages"] == current["total_pages"]:
                    status = "needs_ocr"
                elif current["failed_pages"] or current["no_text_pages"]:
                    status = "partial"
                else:
                    status = "completed"
                errors = [r[0] for r in db.execute(
                    "SELECT error FROM pages WHERE run_id=? AND error IS NOT NULL ORDER BY page", (run_id,)
                )]
                db.execute("UPDATE parse_runs SET status=?,error=?,completed_at=? WHERE id=?",
                           (status, "\n".join(errors)[:5000] or None, _now(), run_id))
        except Exception as exc:
            with self._connection() as db:
                db.execute("UPDATE parse_runs SET status='failed',error=?,completed_at=? WHERE id=?",
                           (f"解析任务失败：{type(exc).__name__}: {exc}"[:2000], _now(), run_id))
        return True
