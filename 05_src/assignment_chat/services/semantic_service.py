"""
semantic_service.py — Service 2: Semantic search over science misconceptions.

Dataset:  "List of common misconceptions about science" (HTML, Wikipedia-style article).
Embeddings: OpenAI text-embedding-3-small via the course API gateway.
Vector DB:  ChromaDB with file persistence at ../data/chroma_db.

On first call the module lazily ingests the HTML file, splits it into chunks,
embeds each chunk, and stores everything in a persistent Chroma collection.
Subsequent calls skip ingestion and query the stored collection directly.
"""
import os
import re
from typing import Optional

import chromadb
from langchain.tools import tool
from langchain_text_splitters import RecursiveCharacterTextSplitter
from openai import OpenAI
from dotenv import load_dotenv

# ── Secrets ───────────────────────────────────────────────────────────────────
_secrets = os.path.join(os.path.dirname(__file__), '..', '.secrets')
if not os.path.exists(_secrets):
    _secrets = os.path.join(os.path.dirname(__file__), '..', '..', '.secrets')
load_dotenv(_secrets)

# ── Paths ─────────────────────────────────────────────────────────────────────
_BASE_DIR       = os.path.dirname(__file__)
_DATA_DIR       = os.path.normpath(os.path.join(_BASE_DIR, '..', 'data'))
_CHROMA_PATH    = os.path.join(_DATA_DIR, 'chroma_db')
_COLLECTION_NAME = "science_misconceptions"
_HTML_PATH      = os.path.normpath(
    os.path.join(_BASE_DIR, '..', '..', 'documents',
                 'List of common misconceptions about science.htm')
)

# ── Singleton state ───────────────────────────────────────────────────────────
_collection: Optional[chromadb.Collection] = None


# ── OpenAI client factory (supports course gateway) ───────────────────────────
def _get_openai_client() -> OpenAI:
    gateway_key = os.getenv("API_GATEWAY_KEY")
    base_url    = os.getenv(
        "OPENAI_BASE_URL",
        "https://k7uffyg03f.execute-api.us-east-1.amazonaws.com/prod/openai/v1",
    )
    if gateway_key:
        return OpenAI(
            base_url=base_url,
            api_key="any value",
            default_headers={"x-api-key": gateway_key},
        )
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# ── Embedding helper ──────────────────────────────────────────────────────────
def _embed_batch(texts: list[str], client: OpenAI) -> list[list[float]]:
    """Embed a batch of texts using text-embedding-3-small."""
    cleaned = [t.replace("\n", " ") for t in texts]
    response = client.embeddings.create(
        input=cleaned,
        model="text-embedding-3-small",
    )
    return [item.embedding for item in response.data]


# ── HTML text extraction ──────────────────────────────────────────────────────
def _extract_text_from_html(path: str) -> str:
    """Strip HTML tags and return clean plain text."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        html = fh.read()

    # Drop script / style blocks entirely
    html = re.sub(
        r"<(script|style)[^>]*>.*?</(script|style)>",
        "",
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # Drop HTML comments
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    # Replace block elements with newlines for natural paragraph breaks
    html = re.sub(
        r"<(p|div|li|h[1-6]|br|dt|dd|tr)[^>]*/?>",
        "\n",
        html,
        flags=re.IGNORECASE,
    )
    # Strip remaining tags
    html = re.sub(r"<[^>]+>", "", html)
    # Decode HTML entities
    import html as _html_module
    html = _html_module.unescape(html)
    # Collapse whitespace
    html = re.sub(r"[ \t]+", " ", html)
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html.strip()


# ── Ingestion ─────────────────────────────────────────────────────────────────
def ingest_documents(data_path: Optional[str] = None) -> None:
    """
    Parse the science-misconceptions HTML, embed the text chunks, and store
    them in a persistent Chroma collection.  This is a no-op if the collection
    already contains documents.

    Args:
        data_path: Override path to the HTML source file (used for testing).
    """
    global _collection

    os.makedirs(_DATA_DIR, exist_ok=True)
    chroma_client = chromadb.PersistentClient(path=_CHROMA_PATH)

    # Re-use the existing collection if it already has data
    try:
        _collection = chroma_client.get_collection(name=_COLLECTION_NAME)
        if _collection.count() > 0:
            return
    except Exception:
        pass  # Collection doesn't exist yet — create it below

    html_path  = data_path or _HTML_PATH
    raw_text   = _extract_text_from_html(html_path)

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=60,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks = [c.strip() for c in splitter.split_text(raw_text) if len(c.strip()) > 80]

    openai_client = _get_openai_client()

    # Embed in batches of 100 (OpenAI API limit per request)
    batch_size = 100
    all_embeddings: list[list[float]] = []
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        all_embeddings.extend(_embed_batch(batch, openai_client))

    ids = [f"doc_{i}" for i in range(len(chunks))]

    _collection = chroma_client.get_or_create_collection(name=_COLLECTION_NAME)
    # Add in Chroma-friendly batches
    for i in range(0, len(chunks), batch_size):
        _collection.add(
            embeddings=all_embeddings[i : i + batch_size],
            documents=chunks[i : i + batch_size],
            ids=ids[i : i + batch_size],
        )


# ── Lazy collection accessor ───────────────────────────────────────────────────
def _get_collection() -> chromadb.Collection:
    global _collection
    if _collection is None:
        ingest_documents()
    return _collection


# ── Tool ──────────────────────────────────────────────────────────────────────
@tool
def search_science_facts(query: str) -> str:
    """
    Search a curated database of common science misconceptions and verified facts.

    Use this tool when the user asks whether a popular belief is accurate, wants
    to fact-check a scientific claim, or is curious about misconceptions in any
    branch of science (biology, physics, chemistry, astronomy, etc.).

    Args:
        query: A natural-language question or claim to look up.

    Returns:
        Up to three relevant excerpts from the science-facts database, formatted
        as numbered findings.
    """
    try:
        collection   = _get_collection()
        openai_client = _get_openai_client()
        query_emb    = _embed_batch([query], openai_client)[0]
        results      = collection.query(
            query_embeddings=[query_emb],
            n_results=3,
        )
        docs = results.get("documents", [[]])[0]
        if not docs:
            return "I could not find relevant science facts for that query in my database."

        formatted = [
            f"**Finding {i + 1}:** {doc.strip()}"
            for i, doc in enumerate(docs)
        ]
        return "\n\n".join(formatted)

    except Exception as exc:
        return (
            f"The science facts database encountered an error: {exc}. "
            "I'll answer from general knowledge instead."
        )
