"""
Ghost Requirement Agent — Embedding Module (Agent 2)
=====================================================
Agent 2 is the Backlog Reconciler. Since embeddings are deterministic
vector operations (not LLM reasoning), this module handles them directly
using the google-genai SDK, keeping the Agno Team clean.

The embedding results (top-N similar tickets + scores) are passed as
pre-formatted context into GhostRequirementTeam.run() so the LLM
coordinator can make routing decisions without needing to call an
external tool mid-run.

Model: gemini-embedding-001 (768-dim vectors via pgvector cosine search)
FR-04: 768-dim vectors | FR-05: Cosine similarity threshold routing
"""
import logging
from typing import List, Tuple, Optional

from google import genai
from google.genai import types

from agents.config import GEMINI_API_KEY

logger = logging.getLogger(__name__)

# Embedding model confirmed available on this API key
EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIMENSIONS = 768


def get_embedding(text: str) -> List[float]:
    """
    Generate a 768-dim RETRIEVAL_DOCUMENT embedding for storing in pgvector.
    Used when indexing backlog tickets or extracted requirements.
    
    Args:
        text: Content to embed (ticket title + description, or requirement text)
        
    Returns:
        List of 768 floats
    """
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is not configured.")
    
    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=text,
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_DOCUMENT",
            output_dimensionality=EMBEDDING_DIMENSIONS,
        ),
    )
    return response.embeddings[0].values


def get_query_embedding(text: str) -> List[float]:
    """
    Generate a 768-dim RETRIEVAL_QUERY embedding for similarity search.
    Used when querying the backlog_index for similar tickets.
    
    Args:
        text: Query text (extracted requirement to search for)
        
    Returns:
        List of 768 floats
    """
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is not configured.")
    
    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=text,
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_QUERY",
            output_dimensionality=EMBEDDING_DIMENSIONS,
        ),
    )
    return response.embeddings[0].values


def search_similar_tickets(
    requirement_text: str,
    db_cursor,
    top_k: int = 3,
) -> Tuple[List[dict], str]:
    """
    Agent 2: Search pgvector backlog_index for tickets similar to the requirement.
    
    FR-05 Similarity routing:
        >= 0.85  → exact_match_found (auto-resolve, no Agent 3)
        0.65-0.85 → contradiction zone (Agent 3 conflict check)
        < 0.65   → new discovery (Agent 3 ticket draft)
    
    Args:
        requirement_text: Extracted requirement text from Agent 1
        db_cursor:        Active psycopg2 cursor (RealDictCursor)
        top_k:            Number of similar tickets to return
        
    Returns:
        Tuple of:
          - similar_tickets: List of dicts with id, title, description, similarity
          - formatted_context: Pre-formatted string ready to pass to GhostRequirementTeam
    """
    # Generate query embedding (RETRIEVAL_QUERY task type for better precision)
    query_vector = get_query_embedding(requirement_text)
    vector_str = f"[{','.join(map(str, query_vector))}]"

    # Cosine similarity search via pgvector (<=> = cosine distance; similarity = 1 - distance)
    db_cursor.execute(
        """
        SELECT id, title, description,
               1 - (ticket_vector <=> %s::vector) AS similarity
        FROM backlog_index
        WHERE ticket_vector IS NOT NULL
        ORDER BY ticket_vector <=> %s::vector
        LIMIT %s
        """,
        (vector_str, vector_str, top_k),
    )
    rows = db_cursor.fetchall()

    similar_tickets = [
        {
            "id": r["id"],
            "title": r["title"],
            "description": r["description"],
            "similarity": float(r["similarity"]),
        }
        for r in rows
    ]

    # Format context string for the GhostRequirementTeam coordinator
    if not similar_tickets:
        formatted_context = "No tickets found in backlog_index. Treat as new discovery."
    else:
        lines = []
        for i, t in enumerate(similar_tickets, 1):
            score = t["similarity"]
            zone = (
                "EXACT MATCH (≥0.85)"
                if score >= 0.85
                else "CONFLICT ZONE (0.65–0.85)"
                if score >= 0.65
                else "NEW DISCOVERY (<0.65)"
            )
            lines.append(
                f"--- Ticket {i} [{zone}] ---\n"
                f"ID: {t['id']}\n"
                f"Title: {t['title']}\n"
                f"Description: {t['description']}\n"
                f"Cosine Similarity: {score:.4f}\n"
            )
        formatted_context = "\n".join(lines)

    top_sim = similar_tickets[0]['similarity'] if similar_tickets else 0.0
    logger.info(
        f"[Agent2/Embedding] Found {len(similar_tickets)} similar tickets. "
        f"Top similarity: {top_sim:.4f}"
    )

    return similar_tickets, formatted_context
