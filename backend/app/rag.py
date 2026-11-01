"""Retrieval-augmented generation over a local ChromaDB collection.

Documents are split into overlapping chunks, embedded with Chroma's built-in
all-MiniLM-L6-v2 ONNX model (runs on CPU, no API key) and stored under
backend/data/chroma. The agents reach them through the tools at the bottom of
this file: semantic search, listing documents, and reading a whole document.
Each search is recorded as an OpenInference
RETRIEVER span, so Arize / Phoenix show which passages were retrieved.
"""

import io
import json
import os
import threading
from functools import cache
from pathlib import Path

import chromadb
from chromadb.config import Settings
from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2
from langchain_text_splitters import RecursiveCharacterTextSplitter
from openinference.semconv.trace import DocumentAttributes, OpenInferenceSpanKindValues, SpanAttributes
from opentelemetry import trace
from pydantic import BaseModel, Field
from pypdf import PdfReader

from .tools import tool

_BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR") or _BACKEND_DIR / "data")
SEED_DIR = _BACKEND_DIR / "knowledge"
# The container image bakes the model in at build time, outside the (mountable) data directory.
MODEL_DIR = Path(os.getenv("EMBEDDING_MODEL_DIR") or DATA_DIR / "onnx_models")
SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf", ".csv", ".json"}

_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)
_write_lock = threading.Lock()
_tracer = trace.get_tracer(__name__)


class _Embedder(ONNXMiniLM_L6_V2):
    # Keep the ~80 MB model with the app's data instead of the user's home directory.
    DOWNLOAD_PATH = MODEL_DIR / ONNXMiniLM_L6_V2.MODEL_NAME


@cache
def _collection():
    client = chromadb.PersistentClient(path=str(DATA_DIR / "chroma"), settings=Settings(anonymized_telemetry=False))
    return client.get_or_create_collection(
        "knowledge",
        embedding_function=_Embedder(),
        configuration={"hnsw": {"space": "cosine"}},
    )


def extract_text(filename: str, data: bytes) -> str:
    """Turn an uploaded file into plain text. Raises ValueError for unsupported types."""
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file type '{suffix}'. Use one of: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
    if suffix == ".pdf":
        return "\n\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
    return data.decode("utf-8", errors="replace")


def ingest(source: str, text: str) -> int:
    """Replace any existing chunks for `source` with fresh ones. Returns the chunk count."""
    chunks = [c for c in _splitter.split_text(text) if c.strip()]
    if not chunks:
        raise ValueError(f"No text found in '{source}'")
    with _write_lock:
        _collection().delete(where={"source": source})
        _collection().add(
            ids=[f"{source}::{i}" for i in range(len(chunks))],
            documents=chunks,
            metadatas=[{"source": source, "chunk": i} for i in range(len(chunks))],
        )
    return len(chunks)


def list_documents() -> list[dict]:
    counts: dict[str, int] = {}
    for meta in _collection().get(include=["metadatas"])["metadatas"] or []:
        counts[meta["source"]] = counts.get(meta["source"], 0) + 1
    return [{"source": s, "chunks": n} for s, n in sorted(counts.items())]


def read_document(source: str) -> list[str]:
    """Return the chunks of `source` in document order (empty if it doesn't exist)."""
    result = _collection().get(where={"source": source}, include=["documents", "metadatas"])
    pairs = sorted(zip(result["metadatas"] or [], result["documents"] or []), key=lambda p: p[0]["chunk"])
    return [doc for _, doc in pairs]


def delete_document(source: str) -> int:
    """Delete every chunk of `source`. Returns how many were removed."""
    with _write_lock:
        ids = _collection().get(where={"source": source})["ids"]
        if ids:
            _collection().delete(ids=ids)
    return len(ids)


def seed() -> None:
    """Load the sample documents in backend/knowledge the first time the store is empty."""
    if _collection().count() > 0:
        return
    for path in sorted(SEED_DIR.glob("*")):
        if path.suffix.lower() in SUPPORTED_EXTENSIONS:
            ingest(path.name, extract_text(path.name, path.read_bytes()))


def search(query: str, k: int = 4) -> list[dict]:
    with _tracer.start_as_current_span("knowledge_base.search") as span:
        span.set_attribute(SpanAttributes.OPENINFERENCE_SPAN_KIND, OpenInferenceSpanKindValues.RETRIEVER.value)
        span.set_attribute(SpanAttributes.INPUT_VALUE, query)
        result = _collection().query(query_texts=[query], n_results=k)
        hits = [
            # Chroma returns cosine distance; report similarity so higher means more relevant.
            {"source": meta["source"], "chunk": meta["chunk"], "score": round(1 - dist, 3), "text": doc}
            for doc, meta, dist in zip(result["documents"][0], result["metadatas"][0], result["distances"][0])
        ]
        for i, hit in enumerate(hits):
            prefix = f"{SpanAttributes.RETRIEVAL_DOCUMENTS}.{i}."
            span.set_attribute(prefix + DocumentAttributes.DOCUMENT_ID, f"{hit['source']}::{hit['chunk']}")
            span.set_attribute(prefix + DocumentAttributes.DOCUMENT_CONTENT, hit["text"])
            span.set_attribute(prefix + DocumentAttributes.DOCUMENT_SCORE, hit["score"])
            span.set_attribute(prefix + DocumentAttributes.DOCUMENT_METADATA, json.dumps({"source": hit["source"]}))
    return hits


class KnowledgeSearchInput(BaseModel):
    query: str = Field(description="What to look up, phrased as a focused search query")
    k: int = Field(default=4, ge=1, le=10, description="Number of passages to return")


@tool(
    "search_knowledge_base",
    "Semantic search over the documents the user uploaded to the knowledge base. Returns the most "
    "relevant passages with their source file and a similarity score (0-1, higher is better).",
    KnowledgeSearchInput,
)
def search_knowledge_base(args: KnowledgeSearchInput) -> str:
    hits = search(args.query, args.k)
    if not hits:
        return json.dumps({"count": 0, "passages": [], "note": "The knowledge base is empty."})
    return json.dumps({"count": len(hits), "passages": hits})


class NoInput(BaseModel):
    pass


@tool(
    "list_knowledge_base",
    "List every document in the knowledge base with its number of chunks.",
    NoInput,
)
def list_knowledge_base(_: NoInput) -> str:
    docs = list_documents()
    return json.dumps({"count": len(docs), "documents": docs})


class ReadDocumentInput(BaseModel):
    source: str = Field(description="Exact document name as returned by list_knowledge_base")
    start_chunk: int = Field(default=0, ge=0, description="First chunk to return, for paging through long documents")
    max_chunks: int = Field(default=15, ge=1, le=30, description="How many chunks to return")


@tool(
    "read_document",
    "Read a document from the knowledge base in order, chunk by chunk (about 1,000 characters each). "
    "Use it to summarise, outline or compare whole documents; page through long ones with start_chunk.",
    ReadDocumentInput,
)
def read_document_tool(args: ReadDocumentInput) -> str:
    chunks = read_document(args.source)
    if not chunks:
        raise ValueError(f"No document named '{args.source}'. Call list_knowledge_base to see the names.")
    end = args.start_chunk + args.max_chunks
    return json.dumps({
        "source": args.source,
        "total_chunks": len(chunks),
        "start_chunk": args.start_chunk,
        "has_more": end < len(chunks),
        "text": "\n".join(chunks[args.start_chunk:end]),
    })
