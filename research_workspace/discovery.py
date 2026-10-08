"""User-triggered arXiv discovery, bounded downloads and durable subscriptions.

No constructor, recommendation or listing method accesses the network. Call search,
import_paper and check_subscription only from an explicit action or a persisted,
enabled subscription. Only user-supplied query strings are sent to arXiv; local
document text is never uploaded. Metadata is not evidence of downloaded full text.

API references: https://info.arxiv.org/help/api/user-manual.html and /api/tou.html.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urljoin, urlsplit
from uuid import uuid4
import xml.etree.ElementTree as ET

import httpx

from .library import Library, _schema_lock

ARXIV_API = "https://export.arxiv.org/api/query"
ATOM = {"a": "http://www.w3.org/2005/Atom", "ar": "http://arxiv.org/schemas/atom",
        "op": "http://a9.com/-/spec/opensearch/1.1/"}
ARXIV_ID = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z][a-z.-]*/\d{7})(?:v[1-9]\d*)?", re.I)
STOP_WORDS = set("a an the of in on for to and or with by from as at is are this that using via based research study paper".split())


class DiscoveryError(ValueError):
    """A recoverable, user-visible discovery error."""


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def arxiv_identifier(value: str) -> str | None:
    """Accept a precise arXiv identifier or official abstract/PDF URL only."""
    value = value.strip()
    if "://" in value:
        try:
            parsed = urlsplit(value)
            invalid = (parsed.scheme not in {"https", "http"} or parsed.hostname not in {"arxiv.org", "export.arxiv.org"}
                       or parsed.username or parsed.password or parsed.port or parsed.query or parsed.fragment)
        except ValueError:
            return None
        if invalid:
            return None
        if not parsed.path.startswith(("/abs/", "/pdf/")):
            return None
        value = parsed.path.split("/", 2)[2]
    value = re.sub(r"^arxiv:", "", value, flags=re.I)
    value = re.sub(r"\.pdf$", "", value, flags=re.I)
    return value if ARXIV_ID.fullmatch(value) else None


def tokens(value: str) -> set[str]:
    words = {word.casefold() for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}|\d{3,}", value)}
    for phrase in re.findall(r"[\u3400-\u9fff]+", value):
        words.update(phrase[index:index + 2] for index in range(max(1, len(phrase) - 1)))
    return words - STOP_WORDS


def _query(value: str) -> str:
    if not isinstance(value, str):
        raise DiscoveryError("检索词必须是文本。")
    result = " ".join(value.split())
    if not result or len(result) > 500:
        raise DiscoveryError("请输入1至500个字符的公开论文标题或关键词。")
    return result


def _bounded_int(value, minimum: int, maximum: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise DiscoveryError(f"{label}必须介于{minimum}和{maximum}之间。")
    return value


class DiscoveryService:
    def __init__(self, library: Library, client: httpx.Client | None = None, *,
                 rate_limit_path: Path | None = None, request_interval: float = 3.0,
                 max_pdf_bytes: int = 50 * 1024 * 1024):
        self.library = library
        self.database_path = library.data_dir / "discovery.sqlite3"
        # The application supplies one shared path to all workspace libraries and
        # worker processes. Standalone use stays inside the requested data folder.
        self.rate_limit_path = Path(rate_limit_path or getattr(library, "network_rate_path", library.data_dir / ".arxiv-network.sqlite3"))
        self.request_interval = max(3.0, float(request_interval)) if client is None else max(0.0, float(request_interval))
        self.max_pdf_bytes = max_pdf_bytes
        self.client = client
        with _schema_lock(self.database_path), self._db() as db:
            if db.execute('PRAGMA journal_mode').fetchone()[0].lower() != 'wal':
                db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS papers (
                    id TEXT PRIMARY KEY, canonical_id TEXT NOT NULL, version INTEGER NOT NULL,
                    record TEXT NOT NULL, first_seen_at TEXT NOT NULL, retrieved_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS paper_canonical ON papers(canonical_id, version);
                CREATE TABLE IF NOT EXISTS searches (
                    cache_key TEXT PRIMARY KEY, query TEXT NOT NULL, field TEXT NOT NULL,
                    sort_by TEXT NOT NULL, retrieved_at TEXT NOT NULL, response TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS imports (
                    paper_id TEXT PRIMARY KEY REFERENCES papers(id), document_id TEXT NOT NULL,
                    downloaded_at TEXT NOT NULL, source_url TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS preferences (
                    paper_id TEXT PRIMARY KEY REFERENCES papers(id), value INTEGER NOT NULL,
                    updated_at TEXT NOT NULL CHECK (value IN (-1,0,1))
                );
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id TEXT PRIMARY KEY, query TEXT NOT NULL, query_key TEXT NOT NULL UNIQUE,
                    field TEXT NOT NULL, interval_hours INTEGER NOT NULL, enabled INTEGER NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL, last_checked_at TEXT,
                    last_success_at TEXT, next_check_at TEXT NOT NULL, check_started_at TEXT,
                    check_token TEXT, last_error TEXT, failure_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS subscription_seen (
                    subscription_id TEXT NOT NULL REFERENCES subscriptions(id),
                    paper_id TEXT NOT NULL REFERENCES papers(id), first_seen_at TEXT NOT NULL,
                    PRIMARY KEY (subscription_id,paper_id)
                );
                CREATE TABLE IF NOT EXISTS subscription_checks (
                    id TEXT PRIMARY KEY, subscription_id TEXT NOT NULL REFERENCES subscriptions(id),
                    checked_at TEXT NOT NULL, status TEXT NOT NULL, new_count INTEGER NOT NULL,
                    cached INTEGER NOT NULL, error TEXT
                );
            """)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.database_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def _network_slot(self):
        self.rate_limit_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.rate_limit_path, timeout=90)
        try:
            db.execute("CREATE TABLE IF NOT EXISTS request_gate (id INTEGER PRIMARY KEY, finished REAL NOT NULL)")
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT finished FROM request_gate WHERE id=1").fetchone()
            delay = max(0.0, row[0] + self.request_interval - time.time()) if row else 0.0
            if delay:
                time.sleep(min(delay, self.request_interval))
            try:
                yield
            finally:
                db.execute("INSERT INTO request_gate VALUES(1,?) ON CONFLICT(id) DO UPDATE SET finished=excluded.finished", (time.time(),))
                db.commit()
        finally:
            db.close()

    def _request(self, url: str, *, params: dict | None = None, max_bytes: int,
                 expected_identifier: str | None = None) -> tuple[bytes, str]:
        client = self.client or httpx.Client(timeout=httpx.Timeout(20, connect=10), follow_redirects=False, trust_env=False,
                                            headers={"User-Agent": "ResearchWorkspace/0.2 (user-initiated academic discovery)"})
        started = time.monotonic()
        try:
            for _ in range(4):
                with self._network_slot(), client.stream("GET", url, params=params, follow_redirects=False) as response:
                    if response.is_redirect:
                        target = urljoin(str(response.url), response.headers.get("location", ""))
                        parsed = urlsplit(target)
                        if not expected_identifier or parsed.scheme != "https" or arxiv_identifier(target) != expected_identifier or not parsed.path.startswith("/pdf/"):
                            raise DiscoveryError("来源重定向未通过校验，未下载该文件。")
                        url, params = target, None
                        continue
                    if response.status_code in {401, 403, 451}:
                        raise DiscoveryError("公开来源限制了本次访问，元数据仍可保留，请稍后重试。")
                    if response.status_code == 429:
                        raise DiscoveryError("公开来源请求过于频繁，请稍后重试。")
                    response.raise_for_status()
                    declared = response.headers.get("content-length", "")
                    if declared.isdigit() and int(declared) > max_bytes:
                        raise DiscoveryError("来源文件超过允许大小，未导入。")
                    chunks = []
                    count = 0
                    for chunk in response.iter_bytes():
                        count += len(chunk)
                        if count > max_bytes:
                            raise DiscoveryError("来源响应超过允许大小，已停止下载。")
                        if time.monotonic() - started > 60:
                            raise DiscoveryError("来源下载超过时间限制，请稍后重试。")
                        chunks.append(chunk)
                    return b"".join(chunks), str(response.url)
            raise DiscoveryError("来源重定向次数过多，未下载文件。")
        except httpx.TimeoutException as exc:
            raise DiscoveryError("公开来源连接超时，请稍后重试。") from exc
        except httpx.HTTPStatusError as exc:
            raise DiscoveryError(f"公开来源暂时无法访问（HTTP {exc.response.status_code}）。") from exc
        except httpx.HTTPError as exc:
            raise DiscoveryError("无法连接公开论文来源，请检查网络后重试。") from exc
        finally:
            if self.client is None:
                client.close()

    @staticmethod
    def _parse_feed(content: bytes, retrieved: str) -> tuple[list[dict], int]:
        if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
            raise DiscoveryError("公开来源返回了不支持的XML声明。")
        try:
            root = ET.fromstring(content)
        except ET.ParseError as exc:
            raise DiscoveryError("公开来源返回的元数据格式无法解析。") from exc
        if root.tag != f"{{{ATOM['a']}}}feed":
            raise DiscoveryError("来源未返回arXiv Atom元数据。")
        result = []
        for entry in root.findall("a:entry", ATOM):
            raw_id = (entry.findtext("a:id", "", ATOM) or "").strip()
            if "/api/errors" in raw_id:
                raise DiscoveryError("arXiv未接受该检索条件，请修改标题或关键词后重试。")
            identifier = arxiv_identifier(raw_id)
            title = " ".join((entry.findtext("a:title", "", ATOM) or "").split())
            if not identifier or not title:
                continue
            base = re.sub(r"v\d+$", "", identifier)
            version_match = re.search(r"v(\d+)$", identifier)
            version = int(version_match[1]) if version_match else 0
            source_url = f"https://arxiv.org/abs/{identifier}"
            pdf_url = None
            for link in entry.findall("a:link", ATOM):
                href = link.get("href", "")
                if link.get("title") == "pdf" and arxiv_identifier(href) == identifier and urlsplit(href).path.startswith("/pdf/"):
                    pdf_url = f"https://arxiv.org/pdf/{identifier}"
            summary = " ".join((entry.findtext("a:summary", "", ATOM) or "").split())
            comment = entry.findtext("ar:comment", "", ATOM) or ""
            code_links = []
            for raw in re.findall(r"https?://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", summary + " " + comment):
                candidate = raw.rstrip(".,")
                if candidate not in {item["url"] for item in code_links}:
                    code_links.append({"url": candidate, "verification": "unverified_metadata_link",
                                       "source_url": source_url, "note": "元数据提供的代码线索，尚未验证仓库关联、许可或复现结果。"})
            result.append({"id": f"arxiv:{identifier}", "arxiv_id": identifier, "canonical_id": base,
                           "version": version, "title": title, "authors": [" ".join((author.findtext("a:name", "", ATOM) or "").split()) for author in entry.findall("a:author", ATOM)],
                           "summary": summary, "categories": [category.get("term", "") for category in entry.findall("a:category", ATOM)],
                           "doi": entry.findtext("ar:doi", "", ATOM) or None,
                           "published": entry.findtext("a:published", "", ATOM), "updated": entry.findtext("a:updated", "", ATOM),
                           "source": "arxiv", "source_url": source_url, "pdf_url": pdf_url,
                           "retrieved_at": retrieved, "metadata_verified": True,
                           "fulltext_status": "metadata_only", "code_links": code_links, "verified_code": False})
        try:
            total = int(root.findtext("op:totalResults", str(len(result)), ATOM))
        except ValueError:
            total = len(result)
        return result, max(len(result), total)

    def _with_import(self, record: dict, db: sqlite3.Connection) -> dict:
        result = dict(record)
        preference = db.execute('SELECT value FROM preferences WHERE paper_id=?',(record['id'],)).fetchone()
        result['feedback'] = preference[0] if preference else 0
        imported = db.execute("SELECT * FROM imports WHERE paper_id=?", (record["id"],)).fetchone()
        result["fulltext_status"] = "metadata_only"
        result["document_id"] = None
        if imported:
            try:
                document = self.library.get_document(imported["document_id"])
                result.update(document_id=document["id"], downloaded_at=imported["downloaded_at"],
                              fulltext_status="downloaded", parse_status=document["current_run"]["status"],
                              fulltext_searchable=bool(document.get("available_run")))
            except KeyError:
                result["fulltext_status"] = "missing_local_file"
        else:
            result["fulltext_searchable"] = False
        return result

    def get_paper(self, paper_id: str) -> dict:
        with self._db() as db:
            row = db.execute("SELECT record FROM papers WHERE id=?", (paper_id,)).fetchone()
            if not row:
                raise KeyError(paper_id)
            return self._with_import(json.loads(row[0]), db)

    def list_papers(self, limit: int = 500) -> list[dict]:
        _bounded_int(limit, 1, 5000, "记录条数")
        with self._db() as db:
            return [self._with_import(json.loads(row[0]), db) for row in db.execute(
                "SELECT record FROM papers ORDER BY retrieved_at DESC LIMIT ?", (limit,))]

    def search(self, query: str, *, field: str = "all", limit: int = 10,
               sort_by: str = "relevance") -> dict:
        query = _query(query)
        _bounded_int(limit, 1, 50, "检索条数")
        if field not in {"all", "title"} or sort_by not in {"relevance", "submittedDate", "lastUpdatedDate"}:
            raise DiscoveryError("不支持该检索字段或排序方式。")
        key = hashlib.sha256(json.dumps([query.casefold(), field, limit, sort_by]).encode()).hexdigest()
        with self._db() as db:
            cache = db.execute("SELECT response,retrieved_at FROM searches WHERE cache_key=?", (key,)).fetchone()
        if cache and datetime.fromisoformat(cache[1]) + timedelta(hours=24) > datetime.now(timezone.utc):
            result = json.loads(cache[0])
            result["cached"] = True
            result["items"] = [self.get_paper(item["id"]) for item in result["items"]]
            return result
        identifier = arxiv_identifier(query)
        params = {"start": 0, "max_results": limit, "sortBy": sort_by, "sortOrder": "descending"}
        if identifier:
            params["id_list"] = identifier
        else:
            # Treat all user input as search terms, not arXiv query operators.
            terms = re.findall(r"[\w-]+", query, re.UNICODE)[:40]
            if not terms:
                raise DiscoveryError("请输入可检索的论文标题或关键词。")
            prefix = "ti" if field == "title" else "all"
            params["search_query"] = " AND ".join(f'{prefix}:"{term}"' for term in terms)
        content, _ = self._request(ARXIV_API, params=params, max_bytes=4 * 1024 * 1024)
        retrieved = now_utc()
        papers, total = self._parse_feed(content, retrieved)
        response = {"query": query, "field": field, "source": "arxiv", "source_url": ARXIV_API,
                    "retrieved_at": retrieved, "items": papers[:limit], "total_results": total,
                    "metadata_only": True, "cached": False,
                    "next_network_refresh_at": (datetime.fromisoformat(retrieved) + timedelta(hours=24)).isoformat()}
        with self._db() as db:
            for paper in papers[:limit]:
                db.execute("""INSERT INTO papers VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                           record=excluded.record,retrieved_at=excluded.retrieved_at""",
                           (paper["id"], paper["canonical_id"], paper["version"], json.dumps(paper, ensure_ascii=False), retrieved, retrieved))
            db.execute("INSERT OR REPLACE INTO searches VALUES(?,?,?,?,?,?)", (key, query, field, sort_by, retrieved, json.dumps(response, ensure_ascii=False)))
        response["items"] = [self.get_paper(paper["id"]) for paper in papers[:limit]]
        return response

    def search_references(self, references: list[str], *, limit_per_reference: int = 3) -> dict:
        if not isinstance(references, list) or not 1 <= len(references) <= 10:
            raise DiscoveryError("一次可检索1至10条公开参考书目。")
        _bounded_int(limit_per_reference, 1, 10, "每条书目检索数量")
        queries = [_query(reference) for reference in references]
        results = []
        for reference in queries:
            try:
                found = self.search(reference, field="all", limit=limit_per_reference)
                results.append({"reference": reference, **found, "error": None})
            except DiscoveryError as exc:
                results.append({"reference": reference, "items": [], "error": str(exc)})
        return {"results": results, "metadata_only": True}

    def import_paper(self, paper_id: str) -> dict:
        paper = self.get_paper(paper_id)
        if paper.get("document_id"):
            return {"document": self.library.get_document(paper["document_id"]), "duplicate": True,
                    "source": {"paper_id": paper_id, "url": paper["pdf_url"], "downloaded_at": paper["downloaded_at"]}}
        url = paper.get("pdf_url")
        if not url or arxiv_identifier(url) != paper["arxiv_id"] or urlsplit(url).scheme != "https":
            raise DiscoveryError("该记录只有元数据，没有已核验的公开PDF来源。")
        content, final_url = self._request(url, max_bytes=self.max_pdf_bytes, expected_identifier=paper["arxiv_id"])
        if not content.lstrip().startswith(b"%PDF-"):
            raise DiscoveryError("来源返回的内容不是PDF，未导入文献库。")
        result = self.library.import_document(content, paper["arxiv_id"].replace("/", "_") + ".pdf")
        downloaded = now_utc()
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO imports VALUES(?,?,?,?)", (paper_id, result["document"]["id"], downloaded, final_url))
        return {**result, "source": {"paper_id": paper_id, "url": final_url, "downloaded_at": downloaded,
                                    "metadata_url": paper["source_url"], "metadata_retrieved_at": paper["retrieved_at"]}}

    @staticmethod
    def _subscription(row) -> dict:
        result = dict(row)
        result["enabled"] = bool(result["enabled"])
        result.pop("query_key", None)
        result.pop("check_token", None)
        return result

    def create_subscription(self, query: str, interval_hours: int = 24, field: str = "all") -> dict:
        query = _query(query)
        _bounded_int(interval_hours, 1, 720, "检查间隔小时数")
        if field not in {"all", "title"}:
            raise DiscoveryError("不支持该订阅检索字段。")
        key = hashlib.sha256(f"{field}:{query.casefold()}".encode()).hexdigest()
        timestamp = now_utc()
        with self._db() as db:
            existing = db.execute("SELECT * FROM subscriptions WHERE query_key=?", (key,)).fetchone()
            if existing:
                return self._subscription(existing)
            identifier = uuid4().hex
            db.execute("""INSERT INTO subscriptions
                       (id,query,query_key,field,interval_hours,enabled,status,created_at,next_check_at)
                       VALUES(?,?,?,?,?,1,'idle',?,?)""", (identifier, query, key, field, interval_hours, timestamp, timestamp))
            return self._subscription(db.execute("SELECT * FROM subscriptions WHERE id=?", (identifier,)).fetchone())

    def list_subscriptions(self) -> list[dict]:
        with self._db() as db:
            return [self._subscription(row) for row in db.execute("SELECT * FROM subscriptions ORDER BY created_at DESC")]

    def update_subscription(self, subscription_id: str, *, enabled: bool | None = None,
                            interval_hours: int | None = None) -> dict:
        if enabled is not None and not isinstance(enabled, bool):
            raise DiscoveryError("订阅启用状态必须是布尔值。")
        if interval_hours is not None:
            _bounded_int(interval_hours, 1, 720, "检查间隔小时数")
        with self._db() as db:
            row = db.execute("SELECT * FROM subscriptions WHERE id=?", (subscription_id,)).fetchone()
            if not row:
                raise KeyError(subscription_id)
            db.execute("UPDATE subscriptions SET enabled=?,interval_hours=? WHERE id=?",
                       (int(enabled) if enabled is not None else row["enabled"], interval_hours or row["interval_hours"], subscription_id))
            return self._subscription(db.execute("SELECT * FROM subscriptions WHERE id=?", (subscription_id,)).fetchone())

    def check_subscription(self, subscription_id: str) -> dict:
        checked = now_utc()
        token = uuid4().hex
        stale_before = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM subscriptions WHERE id=?", (subscription_id,)).fetchone()
            if not row:
                raise KeyError(subscription_id)
            if row["status"] == "checking" and row["check_started_at"] and row["check_started_at"] > stale_before:
                return {"subscription": self._subscription(row), "new_items": [], "items": [], "error": "该订阅正在检查。", "cached": False}
            db.execute("UPDATE subscriptions SET status='checking',check_started_at=?,check_token=? WHERE id=?", (checked, token, subscription_id))
            subscription = dict(row)
        result = None
        error = None
        try:
            result = self.search(subscription["query"], field=subscription["field"], limit=30, sort_by="lastUpdatedDate")
        except Exception as exc:
            error = str(exc) if isinstance(exc, DiscoveryError) else "订阅检查未完成，已有结果已保留，可再次检查。"
        new_items = []
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM subscriptions WHERE id=?", (subscription_id,)).fetchone()
            if row["check_token"] != token:
                return {"subscription": self._subscription(row), "new_items": [], "items": [], "error": "本次检查已由后续任务接替。", "cached": False}
            failures = row["failure_count"] + 1 if error else 0
            delay = min(row["interval_hours"] * 3600, 60 * 2 ** min(failures, 6)) if error else row["interval_hours"] * 3600
            next_check = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
            if result:
                for paper in result["items"]:
                    inserted = db.execute("INSERT OR IGNORE INTO subscription_seen VALUES(?,?,?)", (subscription_id, paper["id"], checked)).rowcount
                    if inserted:
                        new_items.append(paper)
            db.execute("""UPDATE subscriptions SET status=?,last_checked_at=?,last_success_at=?,next_check_at=?,
                       last_error=?,failure_count=?,check_token=NULL,check_started_at=NULL WHERE id=?""",
                       ("error" if error else "idle", checked, row["last_success_at"] if error else checked, next_check, error, failures, subscription_id))
            db.execute("INSERT INTO subscription_checks VALUES(?,?,?,?,?,?,?)",
                       (uuid4().hex, subscription_id, checked, "error" if error else "completed", len(new_items), int(bool(result and result["cached"])), error))
            updated = self._subscription(db.execute("SELECT * FROM subscriptions WHERE id=?", (subscription_id,)).fetchone())
        return {"subscription": updated, "new_items": new_items, "items": result["items"] if result else [],
                "error": error, "cached": bool(result and result["cached"]),
                "retrieved_at": result["retrieved_at"] if result else None}

    def due_checks(self, limit: int = 1) -> list[dict]:
        """Process at most limit due enabled subscriptions; never starts a loop."""
        _bounded_int(limit, 1, 10, "单次检查数量")
        stale = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        with self._db() as db:
            rows = db.execute("""SELECT id FROM subscriptions WHERE enabled=1 AND next_check_at<=?
                               AND (status!='checking' OR check_started_at IS NULL OR check_started_at<=?)
                               ORDER BY next_check_at LIMIT ?""", (now_utc(), stale, limit)).fetchall()
        return [self.check_subscription(row[0]) for row in rows]

    def feedback(self, paper_id: str, value: int) -> dict:
        _bounded_int(value, -1, 1, "反馈值")
        self.get_paper(paper_id)
        updated = now_utc()
        with self._db() as db:
            db.execute("INSERT INTO preferences VALUES(?,?,?) ON CONFLICT(paper_id) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at", (paper_id, value, updated))
        return {"paper_id": paper_id, "value": value, "updated_at": updated}

    def recommendations(self, keywords: str = "", document_ids: list[str] | None = None, limit: int = 20) -> dict:
        _bounded_int(limit, 1, 100, "推荐数量")
        if not isinstance(keywords, str) or len(keywords) > 500:
            raise DiscoveryError("推荐关键词不能超过500个字符。")
        documents = ([doc for doc in self.library.list_documents() if doc.get("is_current_version", True) and not doc.get("archived_at")]
                     if document_ids is None else [self.library.get_document(identifier) for identifier in dict.fromkeys(document_ids)])
        local = [(doc, tokens(doc["title"])) for doc in documents]
        requested = tokens(keywords)
        papers = self.list_papers(5000)
        with self._db() as db:
            preferences = dict(db.execute("SELECT paper_id,value FROM preferences"))
        profiles = [(tokens(paper["title"]), preferences[paper["id"]]) for paper in papers if preferences.get(paper["id"])]
        ranked = []
        for paper in papers:
            title_words = tokens(paper["title"])
            all_words = title_words | tokens(paper["summary"])
            matched = sorted(requested & all_words)
            score = len(matched) / max(1, len(requested)) * 3
            related = []
            reasons = []
            if matched:
                reasons.append("匹配关键词：" + "、".join(matched[:6]))
            for doc, words in local:
                overlap = words & title_words
                if overlap:
                    relevance = len(overlap) / math.sqrt(max(1, len(words) * len(title_words)))
                    related.append({"document_id": doc["id"], "title": doc["title"], "matched_terms": sorted(overlap), "score": round(relevance, 4)})
            related.sort(key=lambda item: -item["score"])
            if related:
                score += related[0]["score"] * 3
                reasons.append("与本地文献标题中的术语相关；尚未基于全文判定研究一致性。")
            preference = preferences.get(paper["id"], 0)
            score += preference * 2
            if preference:
                reasons.append("已根据你对这篇论文的喜欢反馈提高排序。" if preference > 0 else "已根据你对这篇论文的负反馈降低排序。")
            score += sum(value * len(words & title_words) / max(1, len(words | title_words)) * .5 for words, value in profiles)
            if not reasons:
                reasons.append("已检索到的公开元数据，当前没有足够的个人相关性依据。")
            ranked.append({**paper, "score": round(score, 6), "feedback": preference, "reasons": reasons,
                           "related_documents": related[:5]})
        # Show one most recent version per paper; preserve each version in storage.
        latest = {}
        for paper in ranked:
            previous = latest.get(paper["canonical_id"])
            if previous is None or paper["version"] > previous["version"]:
                latest[paper["canonical_id"]] = paper
        items = sorted(latest.values(), key=lambda item: (-item["score"], item["id"]))[:limit]
        return {"items": items, "source": "local_metadata_cache", "network_used": False,
                "method": "关键词与本地文献标题重合度，加持久化喜欢/踩反馈；不是全文语义判定。",
                "code_policy": "仅出现GitHub链接的元数据不计为已核验代码，也不宣称复现成功。"}
