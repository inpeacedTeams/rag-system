import hashlib
import hmac
import logging
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI, Request, UploadFile, File, HTTPException, Depends
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from app.config import settings
from app.documents import DocumentError, extract_file
from app.providers import OpenAIClient, ProviderError
from app.store import VectorStore
from app.embeddings import LocalEmbeddings
from app.web import fetch_page
from app.rag import build_response, NO_INFORMATION

logger = logging.getLogger("rag")
store = VectorStore(settings)
llm = OpenAIClient(settings)
embedder = LocalEmbeddings(settings)
# Fixed-size lock stripes avoid an unbounded per-session lock dictionary.
locks = [threading.RLock() for _ in range(128)]
rate_lock = threading.Lock()
rate_history = defaultdict(deque)
ready = False


def session_lock(workspace: str):
    return locks[int(workspace[:8], 16) % len(locks)]


@asynccontextmanager
async def lifespan(app):
    global ready
    if len(settings.session_secret) < 32:
        raise RuntimeError("SESSION_SECRET должен содержать не менее 32 символов. См. README.md.")
    for attempt in range(10):
        try:
            store.ensure_collection()
            ready = True
            break
        except ProviderError:
            if attempt == 9:
                raise
            time.sleep(2)
    yield
    ready = False


app = FastAPI(title="Document RAG", lifespan=lifespan, docs_url="/api/docs", redoc_url=None)
static = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static), name="static")


def signed_session(workspace: str):
    signature = hmac.new(settings.session_secret.encode(), workspace.encode(), hashlib.sha256).hexdigest()
    return workspace + "." + signature


def read_session(value: str | None):
    if not value or "." not in value:
        return None
    workspace, signature = value.split(".", 1)
    if len(workspace) != 32 or any(c not in "0123456789abcdef" for c in workspace):
        return None
    if len(signature) != 64 or any(c not in "0123456789abcdef" for c in signature):
        return None
    expected = signed_session(workspace).split(".", 1)[1]
    return workspace if hmac.compare_digest(signature, expected) else None


@app.middleware("http")
async def security(request: Request, call_next):
    workspace = read_session(request.cookies.get("rag_session"))
    new_session = workspace is None
    request.state.workspace = workspace or secrets.token_hex(16)
    if request.method in {"POST", "PUT", "DELETE", "PATCH"}:
        if request.headers.get("X-Requested-With") != "DocumentRAG":
            return JSONResponse({"detail": "Недопустимый запрос."}, status_code=403)
        origin = request.headers.get("origin")
        if origin:
            parsed = urlsplit(origin)
            if parsed.netloc.lower() != request.headers.get("host", "").lower() or parsed.scheme not in {"http", "https"}:
                return JSONResponse({"detail": "Запросы с другого сайта запрещены."}, status_code=403)
        # Bound ingestion bodies before FastAPI's multipart parser buffers them.
        limit = settings.max_upload_bytes + 1024 * 1024 if request.url.path == "/api/documents/upload" else 32 * 1024
        length = request.headers.get("content-length")
        if length:
            try:
                if int(length) > limit or int(length) < 0:
                    return JSONResponse({"detail": "Слишком большой запрос."}, status_code=413)
            except ValueError:
                return JSONResponse({"detail": "Некорректный Content-Length."}, status_code=400)
        # Also enforce the limit on chunked requests; never trust Content-Length alone.
        chunks = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > limit:
                return JSONResponse({"detail": "Слишком большой запрос."}, status_code=413)
            chunks.append(chunk)
        request._body = b"".join(chunks)
    response = await call_next(request)
    if new_session:
        response.set_cookie("rag_session", signed_session(request.state.workspace), max_age=60 * 60 * 24 * 365,
                            httponly=True, secure=settings.secure_cookie, samesite="strict")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    response.headers["Cache-Control"] = "no-store"
    return response


def workspace(request: Request):
    return request.state.workspace


def limit_requests(ws: str):
    now = time.monotonic()
    with rate_lock:
        # Expire inactive entries, preventing an unbounded rate-limit map.
        for key in list(rate_history):
            if not rate_history[key] or rate_history[key][-1] < now - 60:
                del rate_history[key]
        history = rate_history[ws]
        while history and history[0] < now - 60:
            history.popleft()
        if len(history) >= 20 or sum(len(items) for items in rate_history.values()) >= 120:
            raise HTTPException(429, "Слишком много запросов. Подождите минуту.")
        history.append(now)


@app.exception_handler(DocumentError)
async def document_error(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(ProviderError)
async def provider_error(request, exc):
    logger.warning("Provider request failed: %s", exc)
    return JSONResponse({"detail": str(exc)}, status_code=503)


@app.api_route("/", methods=["GET", "HEAD"])
def home():
    return FileResponse(static / "index.html")


@app.get("/health")
def health():
    return {"status": "ok" if ready else "starting"}


@app.get("/api/status")
def status():
    return {"configured": bool(settings.chat_api_key), "max_upload_mb": 20, "max_documents": settings.max_documents}


@app.get("/api/documents")
def documents(ws: str = Depends(workspace)):
    return {"documents": store.documents(ws)}


def ingest(ws: str, name: str, kind: str, chunks: list, url=None):
    chunks = embedder.prepare_chunks(chunks)
    if len(chunks) > settings.max_chunks:
        raise DocumentError(f"Документ слишком большой: максимум {settings.max_chunks} чанков.")
    with session_lock(ws):
        if len(store.documents(ws)) >= settings.max_documents:
            raise DocumentError("Достигнут лимит 30 документов. Удалите лишние документы.")
        document = {"document_id": str(uuid.uuid4()), "name": name, "kind": kind, "url": url,
                    "created_at": datetime.now(timezone.utc).isoformat(), "chunk_count": len(chunks)}
        vectors = embedder.embeddings([chunk.text for chunk in chunks])
        store.add(ws, document, chunks, vectors)
        return document


@app.post("/api/documents/upload", status_code=201)
def upload(file: UploadFile = File(...), ws: str = Depends(workspace)):
    limit_requests(ws)
    name = (file.filename or "document").replace("\\", "/").split("/")[-1][:200]
    try:
        data = file.file.read(settings.max_upload_bytes + 1)
    finally:
        file.file.close()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, "Максимальный размер файла: 20 МБ.")
    chunks = extract_file(name, data, settings.max_pages)
    return {"document": ingest(ws, name, name.rsplit(".", 1)[-1].lower(), chunks)}


class URLBody(BaseModel):
    url: str = Field(min_length=8, max_length=2048)


@app.post("/api/documents/url", status_code=201)
def add_url(body: URLBody, ws: str = Depends(workspace)):
    limit_requests(ws)
    title, url, chunks = fetch_page(body.url, settings.max_site_bytes)
    return {"document": ingest(ws, title, "web", chunks, url)}


@app.delete("/api/documents")
def clear(ws: str = Depends(workspace)):
    limit_requests(ws)
    with session_lock(ws):
        store.clear(ws)
    return {"deleted": True}


@app.delete("/api/documents/{document_id}")
def delete_document(document_id: uuid.UUID, ws: str = Depends(workspace)):
    limit_requests(ws)
    with session_lock(ws):
        store.clear(ws, str(document_id))
    return {"deleted": True}


class ChatBody(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


@app.post("/api/chat")
def chat(body: ChatBody, ws: str = Depends(workspace)):
    limit_requests(ws)
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "Введите вопрос.")
    # Keep clear/delete from racing an in-flight answer for this session.
    with session_lock(ws):
        if not store.documents(ws):
            return {"answer": NO_INFORMATION, "sources": [], "found": False}
        vector = embedder.embeddings([question], query=True)[0]
        hits = store.search(ws, vector)
        if not hits:
            return {"answer": NO_INFORMATION, "sources": [], "found": False}
        evidence = [{"source_id": i + 1, "text": hit["payload"]["text"]} for i, hit in enumerate(hits)]
        result = llm.answer(question, evidence)
        response = build_response(result, hits)
        if response["found"] and not llm.verify(question, result["claims"]):
            return {"answer": NO_INFORMATION, "sources": [], "found": False}
        return response
