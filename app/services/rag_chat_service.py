"""RAG chat service – embed user query, retrieve tenant-scoped chunks, generate LLM answer.

Super-admin flows may pass tenant_id=None to scope retrieval to "global" (NULL-tenant) documents.
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

from openai import OpenAI
from sqlalchemy.orm import Session

from app.services.similarity_search_service import embed_query, search_similar_chunks

logger = logging.getLogger(__name__)

# Groq-hosted model (uses OpenAI-compatible API)
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "llama-3.3-70b-versatile"


def _build_context(chunks: List[dict]) -> str:
    """Build a context block from retrieved chunks for the LLM prompt."""
    if not chunks:
        return ""
    parts = []
    for i, chunk in enumerate(chunks, 1):
        source = chunk.get("filename") or f"Document #{chunk['document_text_extraction_id']}"
        parts.append(
            f"[Source {i}: {source} (chunk {chunk['chunk_index']}, similarity {chunk['similarity']})]\n"
            f"{chunk['chunk_text']}"
        )
    return "\n\n---\n\n".join(parts)


SYSTEM_PROMPT = """You are a helpful document assistant for the "AskYourDocument" platform.
Your job is to answer the user's question based ONLY on the provided context from their documents.

Rules:
- Answer based strictly on the provided context. Do not use outside knowledge.
- If the context does not contain enough information to answer, say so clearly.
- Cite which source(s) you used when answering (e.g., "According to [Source 1: filename.pdf]...").
- Be concise and direct.
- If the user asks something completely unrelated to the documents, politely redirect them."""


def chat(
    db: Session,
    tenant_id: Optional[int],
    user_query: str,
    top_k: int = 5,
    similarity_threshold: float = 0.3,
    model: Optional[str] = None,
) -> dict:
    """
    Full RAG pipeline:
    1. Embed the user query
    2. Retrieve top-k similar chunks scoped to tenant_id
    3. Build prompt with context
    4. Call LLM and return answer + sources
    """
    # 1. Embed query
    logger.info("[RAG] Embedding query for tenant_id=%s: %s", tenant_id, user_query[:100])
    query_embedding = embed_query(user_query)

    # 2. Retrieve similar chunks (tenant-scoped; tenant_id=None searches global documents)
    chunks = search_similar_chunks(
        db=db,
        query_embedding=query_embedding,
        tenant_id=tenant_id,
        top_k=top_k,
        similarity_threshold=similarity_threshold,
    )
    logger.info("[RAG] Retrieved %d chunks for tenant_id=%s", len(chunks), tenant_id)

    if not chunks:
        return {
            "answer": "I couldn't find any relevant information in your documents to answer this question. Please make sure you've uploaded documents related to your query.",
            "sources": [],
            "chunks_retrieved": 0,
        }

    # 3. Build context and prompt
    context = _build_context(chunks)
    user_message = (
        f"Context from the user's documents:\n\n{context}\n\n---\n\n"
        f"User question: {user_query}"
    )

    # 4. Call LLM via Groq (OpenAI-compatible)
    api_key = os.getenv("GROK_API_KEY")
    if not api_key:
        raise RuntimeError("GROK_API_KEY environment variable is not set")

    client = OpenAI(base_url=GROQ_BASE_URL, api_key=api_key)
    llm_model = model or DEFAULT_MODEL

    logger.info("[RAG] Calling LLM model=%s", llm_model)
    completion = client.chat.completions.create(
        model=llm_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        temperature=0.2,
        max_tokens=1024,
    )

    answer = completion.choices[0].message.content

    # Build source references
    sources = []
    seen = set()
    for chunk in chunks:
        key = chunk["document_text_extraction_id"]
        if key not in seen:
            seen.add(key)
            sources.append({
                "document_id": key,
                "filename": chunk.get("filename"),
                "similarity": chunk["similarity"],
            })

    return {
        "answer": answer,
        "sources": sources,
        "chunks_retrieved": len(chunks),
    }
