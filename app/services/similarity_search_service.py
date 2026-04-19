"""Similarity search over document chunk embeddings using pgvector cosine distance."""

from __future__ import annotations

import logging
import os
from typing import List, Optional

from google import genai
from google.genai import types as genai_types
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.document_chunk import DocumentChunk, EMBEDDING_DIM, EMBEDDING_MODEL_GOOGLE

logger = logging.getLogger(__name__)


def embed_query(query_text: str) -> List[float]:
    """Embed a user query using Google gemini-embedding-001 with RETRIEVAL_QUERY task type."""
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY environment variable is not set")

    client = genai.Client(api_key=api_key)
    result = client.models.embed_content(
        model=EMBEDDING_MODEL_GOOGLE,
        contents=[query_text],
        config=genai_types.EmbedContentConfig(
            task_type="RETRIEVAL_QUERY",
            output_dimensionality=EMBEDDING_DIM,
        ),
    )
    return result.embeddings[0].values


def search_similar_chunks(
    db: Session,
    query_embedding: List[float],
    tenant_id: int,
    top_k: int = 5,
    similarity_threshold: float = 0.3,
) -> List[dict]:
    """
    Find the top-k most similar document chunks for a given tenant.

    Uses pgvector cosine distance operator (<=>).
    Only returns chunks belonging to the specified tenant_id.
    """
    embedding_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

    sql = text("""
        SELECT
            dc.id,
            dc.chunk_text,
            dc.chunk_index,
            dc.document_text_extraction_id,
            dte.filename,
            1 - (dc.embedding <=> :embedding ::vector) AS similarity
        FROM document_chunks dc
        JOIN document_text_extractions dte
            ON dte.id = dc.document_text_extraction_id
        WHERE dc.tenant_id = :tenant_id
          AND dc.embedding IS NOT NULL
          AND 1 - (dc.embedding <=> :embedding ::vector) >= :threshold
        ORDER BY dc.embedding <=> :embedding ::vector
        LIMIT :top_k
    """)

    rows = db.execute(
        sql,
        {
            "embedding": embedding_str,
            "tenant_id": tenant_id,
            "threshold": similarity_threshold,
            "top_k": top_k,
        },
    ).fetchall()

    return [
        {
            "chunk_id": row.id,
            "chunk_text": row.chunk_text,
            "chunk_index": row.chunk_index,
            "document_text_extraction_id": row.document_text_extraction_id,
            "filename": row.filename,
            "similarity": round(float(row.similarity), 4),
        }
        for row in rows
    ]
