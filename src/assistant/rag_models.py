"""Typed data models for RAG retrieval results."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SourceType(Enum):
    """Types of content that can be retrieved via RAG search."""

    TRANSCRIPT = "transcript"
    SUMMARY = "summary"
    SCREENSHOT = "screenshot"
    ASSISTANT_MESSAGE = "assistant_message"


class RagScope(Enum):
    """Scope of retrieval operations."""

    CURRENT_SESSION = "current_session"
    ANY_SESSION = "any_session"


@dataclass
class RetrievedChunk:
    """A single chunk returned from RAG search."""

    chunk_id: int
    document_id: int
    source_type: SourceType
    source_id: int
    session_id: int
    timestamp: int
    title: Optional[str]
    content: str
    score: float


@dataclass
class RetrievalBundle:
    """A collection of retrieved chunks for a query."""

    query: str
    scope: RagScope
    chunks: list[RetrievedChunk] = field(default_factory=list)