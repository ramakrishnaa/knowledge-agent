import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()  # before importing the agent, so the LLM client sees .env values

from .telemetry import setup_tracing  # noqa: E402

TRACING = setup_tracing()  # before the agent imports, so every client is instrumented

import openai  # noqa: E402
from fastapi import FastAPI, HTTPException, UploadFile  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from . import rag  # noqa: E402
from .agent import AGENTS, MODEL, delete_session as delete_thread, run_turn, session_exists  # noqa: E402
from .tools import TOOLS  # noqa: E402

log = logging.getLogger("uvicorn.error")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Load the sample documents in the background: the first run downloads the embedding model.
    async def seed():
        try:
            await asyncio.to_thread(rag.seed)
        except Exception:
            log.exception("Seeding the knowledge base failed")

    task = asyncio.create_task(seed())
    yield
    task.cancel()


app = FastAPI(title="Knowledge Agent", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    # Comma-separated; only needed when the UI calls the API from a different origin.
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

# One lock per conversation so two requests can't interleave in the same history.
session_locks: dict[str, asyncio.Lock] = {}


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    session_id: str | None = None


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


def _error_message(exc: Exception) -> str:
    if isinstance(exc, openai.AuthenticationError):
        return "Authentication failed: set LLM_API_KEY in backend/.env"
    if isinstance(exc, openai.RateLimitError):
        return "Rate limited by the LLM provider (free-tier limit); please wait and retry."
    if isinstance(exc, openai.APIStatusError):
        return f"LLM API error {exc.status_code}: {exc.message}"
    if isinstance(exc, openai.APIConnectionError):
        return "Could not reach the LLM server; check LLM_BASE_URL and your network."
    return f"Unexpected error: {exc}"


@app.get("/api/health")
async def health():
    return {"status": "ok", "model": MODEL, "tracing": TRACING}


@app.get("/api/tools")
async def list_tools():
    return [{"name": t.name, "description": t.description} for t in TOOLS.values()]


@app.get("/api/agents")
async def list_agents():
    return [
        {
            "name": name,
            "title": agent.title,
            "description": agent.description,
            "tools": [{"name": t, "description": TOOLS[t].description} for t in agent.tools],
        }
        for name, agent in AGENTS.items()
    ]


@app.post("/api/chat")
async def chat(req: ChatRequest):
    session_id = req.session_id or str(uuid.uuid4())
    lock = session_locks.setdefault(session_id, asyncio.Lock())

    async def event_stream():
        yield _sse({"type": "session", "session_id": session_id})
        async with lock:
            try:
                async for event in run_turn(session_id, req.message):
                    yield _sse(event)
            except Exception as exc:
                log.exception("Chat turn failed")
                yield _sse({"type": "error", "message": _error_message(exc)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    if not await session_exists(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    await delete_thread(session_id)
    session_locks.pop(session_id, None)
    return {"deleted": session_id}


# --- knowledge base -------------------------------------------------------------


@app.get("/api/documents")
async def list_documents():
    return await asyncio.to_thread(rag.list_documents)


@app.post("/api/documents")
async def upload_document(file: UploadFile):
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File is larger than 10 MB")
    name = file.filename or "upload.txt"
    try:
        text = rag.extract_text(name, data)
        chunks = await asyncio.to_thread(rag.ingest, name, text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"source": name, "chunks": chunks}


class TextDocument(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=MAX_UPLOAD_BYTES)


@app.post("/api/documents/text")
async def add_text_document(doc: TextDocument):
    name = doc.title.strip()
    if not name.lower().endswith((".txt", ".md")):
        name += ".txt"
    try:
        chunks = await asyncio.to_thread(rag.ingest, name, doc.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"source": name, "chunks": chunks}


@app.delete("/api/documents/{source}")
async def delete_document(source: str):
    if not await asyncio.to_thread(rag.delete_document, source):
        raise HTTPException(status_code=404, detail="Document not found")
    return {"deleted": source}
