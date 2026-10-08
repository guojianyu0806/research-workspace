"""Durable PDF evidence storage for the first, non-LLM implementation increment."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import threading
from typing import Iterator
from uuid import uuid4

import pymupdf


# PyMuPDF objects must never be used concurrently by API threads. The parser runs
# in a separate process; its lock therefore does not block API rendering.
MUPDF_LOCK = threading.RLock()
PARSER_VERSION = f"pymupdf-{pymupdf.VersionBind}/text-blocks-v1"


class LibraryError(ValueError):
    """An invalid file or an invalid library operation."""


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
        with self._connection() as db:
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
                """
            )

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
        return dict(row)

    def _document(self, db: sqlite3.Connection, document_id: str) -> dict:
        row = db.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        if row is None:
            raise KeyError(document_id)
        result = dict(row)
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

    def list_documents(self) -> list[dict]:
        with self._connection() as db:
            ids = db.execute("SELECT id FROM documents ORDER BY created_at DESC").fetchall()
            return [self._document(db, row[0]) for row in ids]

    def get_document(self, document_id: str) -> dict:
        with self._connection() as db:
            return self._document(db, document_id)

    def get_run(self, run_id: str) -> dict:
        with self._connection() as db:
            return self._run(db, run_id)

    def list_runs(self, document_id: str) -> list[dict]:
        with self._connection() as db:
            self._document(db, document_id)
            return [dict(row) for row in db.execute(
                "SELECT * FROM parse_runs WHERE document_id=? ORDER BY version DESC", (document_id,)
            )]

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
                          "text": "", "error": None}
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
            cursor = db.execute(
                "UPDATE parse_runs SET status='queued', completed_at=NULL WHERE status='running'"
            )
            return cursor.rowcount

    def _claim_run(self) -> dict | None:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id FROM parse_runs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE parse_runs SET status='running',started_at=?,error=NULL WHERE id=?", (_now(), row[0]))
            return self._run(db, row[0])

    @staticmethod
    def _page_blocks(source_page: pymupdf.Page) -> list[dict]:
        width, height = source_page.rect.width, source_page.rect.height
        result = []
        for raw in source_page.get_text("blocks", sort=True):
            if raw[6] != 0 or not raw[4].strip():
                continue
            # get_text coordinates are unrotated and crop-relative. Applying the
            # page rotation yields the exact coordinate system used by get_pixmap.
            rect = (pymupdf.Rect(raw[:4]) * source_page.rotation_matrix) & source_page.rect
            if rect.is_empty:
                continue
            bbox = [max(0.0, min(1.0, value)) for value in
                    (rect.x0 / width, rect.y0 / height, rect.x1 / width, rect.y1 / height)]
            result.append({"id": uuid4().hex, "text": raw[4].strip(), "bbox": bbox})
        return result

    def _save_page(self, run_id: str, page: int, width: float, height: float,
                   rotation: int, blocks: list[dict], error: str | None = None) -> None:
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
                """INSERT INTO pages(run_id,page,width,height,rotation,status,text,error)
                   VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(run_id,page) DO UPDATE SET
                   width=excluded.width,height=excluded.height,rotation=excluded.rotation,
                   status=excluded.status,text=excluded.text,error=excluded.error""",
                (run_id, page, width, height, rotation, status, text, error),
            )
            db.executemany(
                "INSERT INTO blocks(id,run_id,page,ordinal,text,search_text,bbox) VALUES (?,?,?,?,?,?,?)",
                [(b["id"], run_id, page, index, b["text"], _normalize(b["text"]), json.dumps(b["bbox"]))
                 for index, b in enumerate(blocks)],
            )
            self._update_counts(db, run_id)

    @staticmethod
    def _update_counts(db: sqlite3.Connection, run_id: str) -> None:
        counts = dict(db.execute("SELECT status,COUNT(*) FROM pages WHERE run_id=? GROUP BY status", (run_id,)))
        db.execute(
            "UPDATE parse_runs SET pages_done=?,text_pages=?,no_text_pages=?,failed_pages=? WHERE id=?",
            (sum(counts.values()), counts.get("parsed", 0), counts.get("no_extractable_text", 0),
             counts.get("failed", 0), run_id),
        )

    def process_next_job(self) -> bool:
        run = self._claim_run()
        if run is None:
            return False
        run_id = run["id"]
        try:
            with self._connection() as db:
                done = {row[0] for row in db.execute(
                    "SELECT page FROM pages WHERE run_id=? AND status!='failed'", (run_id,)
                )}
            with MUPDF_LOCK, pymupdf.open(self._source_path(run["document_id"])) as pdf:
                for page_number in range(1, run["total_pages"] + 1):
                    if page_number in done:
                        continue
                    width = height = 0.0
                    rotation = 0
                    try:
                        source_page = pdf.load_page(page_number - 1)
                        width, height, rotation = source_page.rect.width, source_page.rect.height, source_page.rotation
                        blocks = self._page_blocks(source_page)
                        self._save_page(run_id, page_number, width, height, rotation, blocks)
                    except Exception as exc:
                        self._save_page(run_id, page_number, width, height, rotation, [],
                                        error=f"第{page_number}页解析失败：{type(exc).__name__}: {exc}"[:1000])
            with self._connection() as db:
                db.execute("BEGIN IMMEDIATE")
                self._update_counts(db, run_id)
                current = self._run(db, run_id)
                if current["failed_pages"] == current["total_pages"]:
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
