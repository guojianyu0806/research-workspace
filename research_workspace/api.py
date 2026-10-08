"""Local-only API for immutable PDF imports and traceable text evidence."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .library import Library
from .accounts import Accounts
from .advanced_api import build_router, library_for
from .intelligence import IntelligenceService
from .model_settings import router as model_settings_router

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_ROOT = Path(__file__).resolve().parent / "static"


class BodyLimitMiddleware:
    """Bound multipart bodies even when Content-Length is omitted."""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse({"detail": "无效的请求长度。"}, 400)(scope, receive, send)
        if declared > self.max_bytes:
            return await JSONResponse({"detail": "上传内容超过允许大小。"}, 413)(scope, receive, send)
        consumed = 0
        exceeded = False
        replacement_sent = False

        async def limited_receive():
            nonlocal consumed, exceeded
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > self.max_bytes:
                    exceeded = True
                    raise HTTPException(413, "上传内容超过允许大小。")
            return message

        async def limited_send(message):
            nonlocal replacement_sent
            if exceeded:
                # Multipart parsing may wrap the receive exception into a generic
                # 400. Preserve the true failure reason at the outer ASGI boundary.
                if not replacement_sent:
                    await JSONResponse({"detail": "上传内容超过允许大小。"}, 413)(scope, receive, send)
                    replacement_sent = True
                return
            await send(message)

        try:
            return await self.app(scope, limited_receive, limited_send)
        except Exception:
            if not exceeded:
                raise
            if not replacement_sent:
                await JSONResponse({"detail": "上传内容超过允许大小。"}, 413)(scope, receive, send)


def create_app(data_dir: Path | str | None = None, *, start_worker: bool = True,
               max_upload_mb: int | None = None) -> FastAPI:
    directory = Path(data_dir or os.getenv("RESEARCH_DATA_DIR", str(PROJECT_ROOT / "data"))).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    upload_mb = max_upload_mb or int(os.getenv("RESEARCH_MAX_UPLOAD_MB", "50"))
    if not 1 <= upload_mb <= 200:
        raise ValueError("RESEARCH_MAX_UPLOAD_MB必须在1至200之间")
    max_bytes = upload_mb * 1024 * 1024
    library = Library(directory)
    library.network_rate_path = directory / '.arxiv-network.sqlite3'
    worker: subprocess.Popen | None = None
    activity_worker: subprocess.Popen | None = None
    model_settings_lock = threading.Lock()

    def configure_model(api_key, model):
        nonlocal activity_worker
        with model_settings_lock:
            for workspace in app.state.accounts.workspace_directories():
                workspace_library = Library(workspace)
                with workspace_library._connection() as db:
                    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_jobs'").fetchone():
                        if db.execute("SELECT 1 FROM research_jobs WHERE status IN ('queued','running') LIMIT 1").fetchone():
                            raise ValueError('请先等待或取消正在排队和执行的研究任务，再更换模型配置。')
            if activity_worker is not None and activity_worker.poll() is None:
                activity_worker.terminate()
                activity_worker.wait(timeout=5)
            os.environ['RESEARCH_MODEL_BASE_URL'] = 'https://api.deepseek.com'
            os.environ['RESEARCH_MODEL'] = model
            os.environ['RESEARCH_MODEL_API_KEY'] = api_key
            if start_worker:
                with (directory / 'worker.log').open('ab') as output:
                    activity_worker = subprocess.Popen(
                        [sys.executable,'-m','research_workspace.activity_worker','--data-dir',str(directory)],
                        cwd=PROJECT_ROOT,stdout=output,stderr=output,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,
                    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal worker, activity_worker
        logfile = None
        if start_worker:
            logfile = (directory / "worker.log").open("ab")
            worker = subprocess.Popen(
                [sys.executable, "-m", "research_workspace.worker", "--data-dir", str(directory)],
                cwd=PROJECT_ROOT, stdout=logfile, stderr=logfile,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            activity_worker = subprocess.Popen(
                [sys.executable, '-m', 'research_workspace.activity_worker', '--data-dir', str(directory)],
                cwd=PROJECT_ROOT, stdout=logfile, stderr=logfile,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
        try:
            yield
        finally:
            for child in (worker, activity_worker):
                if child is not None and child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=5)
            if logfile:
                logfile.close()

    app = FastAPI(title="研序 Research Workspace", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None)
    app.state.library = library
    app.state.accounts = Accounts(directory)
    app.state.libraries = {'default':library}
    app.state.libraries_lock = threading.Lock()
    # Research enqueue/retry and configuration replacement form one critical
    # section, so a job cannot slip between the idle check and worker restart.
    app.state.model_settings_lock = model_settings_lock
    app.state.configure_model = configure_model

    @app.exception_handler(KeyError)
    async def missing_resource(request: Request, exc: KeyError):
        return JSONResponse({"detail": "文献、页面或处理记录不存在。"}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid_input(request: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.middleware("http")
    async def local_origin_guard(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin:
            try:
                parsed = urlsplit(origin)
            except ValueError:
                return JSONResponse({"detail": "无效的请求来源。"}, 403)
            if parsed.scheme != request.url.scheme or parsed.netloc != request.headers.get("host"):
                return JSONResponse({"detail": "当前本地工作台不接受其他站点的请求。"}, 403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "当前本地工作台不接受跨站访问。"}, 403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' blob:; connect-src 'self'; object-src 'none'; "
            "frame-ancestors 'none'; base-uri 'self'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.add_middleware(BodyLimitMiddleware, max_bytes=max_bytes + 1024 * 1024)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__, "mode": "local",
                "worker_running": worker is not None and worker.poll() is None,
                "activity_worker_running": activity_worker is not None and activity_worker.poll() is None}

    @app.get("/api/capabilities")
    def capabilities(library:Library=Depends(library_for)):
        ocr = library.ocr_status()
        intelligence = IntelligenceService(library).capabilities()
        return {"mode": "local", "ocr": ocr['available'], "llm": intelligence['configured'],
                'ocr_details':ocr, 'intelligence':intelligence,
                "max_upload_mb": upload_mb, "search": "literal",
                "worker_running": worker is not None and worker.poll() is None,
                'activity_worker_running':activity_worker is not None and activity_worker.poll() is None}

    @app.get("/api/documents")
    def documents(include_archived:bool=False,library:Library=Depends(library_for)):
        return {"documents": library.list_documents(include_archived=include_archived)}

    @app.post("/api/documents")
    async def upload(file: UploadFile,library:Library=Depends(library_for)):
        chunks = []
        total = 0
        try:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(413, f"单个PDF不能超过{upload_mb} MB。")
                chunks.append(chunk)
            if total == 0:
                raise HTTPException(400, "文件为空，请选择有效的PDF。")
            result = await run_in_threadpool(library.import_document, b"".join(chunks), file.filename or "untitled.pdf")
            return JSONResponse(result, status_code=200 if result["duplicate"] else 202)
        finally:
            await file.close()

    @app.get("/api/documents/{document_id}")
    def document(document_id: str,library:Library=Depends(library_for)):
        return {"document": library.get_document(document_id)}

    @app.get("/api/documents/{document_id}/parse-runs")
    def parse_runs(document_id: str,library:Library=Depends(library_for)):
        return {"runs": library.list_runs(document_id)}

    @app.post("/api/documents/{document_id}/parse-runs", status_code=202)
    def reparse(document_id: str,library:Library=Depends(library_for)):
        return {"run": library.create_run(document_id)}

    @app.get("/api/jobs/{run_id}")
    def job(run_id: str,library:Library=Depends(library_for)):
        return {"run": library.get_run(run_id)}

    @app.get("/api/documents/{document_id}/pages/{page}")
    def page_details(document_id: str, page: int, run_id: str | None = None,library:Library=Depends(library_for)):
        return library.get_page(document_id, page, run_id=run_id)

    @app.get("/api/documents/{document_id}/pages/{page}/image")
    def page_image(document_id: str, page: int, scale: float = Query(1.25, ge=0.5, le=2.0),library:Library=Depends(library_for)):
        return Response(library.render_page(document_id, page, scale=scale), media_type="image/png")

    @app.get("/api/search")
    def search(q: str = Query(..., min_length=1, max_length=200),
               document_id: str | None = None, run_id: str | None = None,
               limit: int = Query(30, ge=1, le=100),library:Library=Depends(library_for)):
        if not q.strip():
            raise HTTPException(400, "请输入需要查找的关键词。")
        return {"query": q, "mode": "literal",
                "hits": library.search(q, document_id=document_id, run_id=run_id, limit=limit)}

    app.include_router(build_router())
    app.include_router(model_settings_router)

    @app.get("/")
    def index():
        return FileResponse(STATIC_ROOT / "index.html")

    # StaticFiles checks lazily so backend-only unit tests need no frontend fixture.
    app.mount("/static", StaticFiles(directory=STATIC_ROOT, check_dir=False), name="static")
    return app


app = create_app()
