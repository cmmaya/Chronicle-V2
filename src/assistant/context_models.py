"""Context models for assistant retrieval and clarification."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class SessionCandidate:
    """Represents a session that matched during retrieval."""
    session_id: int
    session_name: str
    start_timestamp: int
    relevance_score: float = 1.0

    def to_prompt_text(self) -> str:
        """Format session candidate for prompt inclusion."""
        dt = datetime.fromtimestamp(self.start_timestamp)
        date_str = dt.strftime("%Y-%m-%d %H:%M")
        return f"[Session {self.session_id}] {self.session_name} ({date_str})"


@dataclass
class TranscriptExcerpt:
    """Represents a transcript excerpt from a session."""
    session_id: int
    session_name: str
    timestamp: int
    source: str  # 'microphone' or 'system'
    text: str

    def to_prompt_text(self) -> str:
        """Format transcript excerpt for prompt inclusion."""
        dt = datetime.fromtimestamp(self.timestamp)
        time_str = dt.strftime("%H:%M:%S")
        source_label = "Mic" if self.source == "microphone" else "Sys"
        return f"[{time_str}] ({source_label}): {self.text}"


@dataclass
class SummaryExcerpt:
    """Represents a summary excerpt from a session."""
    session_id: int
    session_name: str
    summary_type: str  # 'key_points', 'action_items', 'decisions', 'full'
    content: str

    def to_prompt_text(self) -> str:
        """Format summary excerpt for prompt inclusion."""
        return f"[Summary - {self.summary_type}]: {self.content}"


SCREENSHOT_SECTION_HEADER = "## Screenshots (index; full details only for the most relevant)"


@dataclass
class ScreenshotReference:
    """A screenshot of the session, as the assistant sees it (BU108).

    ``tier`` is 'preview' (one-line index card) or 'full' (card plus the AI
    summary, visible text and keywords). ``filepath`` is kept for the UI and
    is never rendered into the prompt.
    """
    session_id: int
    session_name: str
    timestamp: int
    filepath: str
    description: Optional[str] = None
    screenshot_id: Optional[int] = None
    preview: str = ""
    tier: str = "preview"
    ai_summary: str = ""
    visible_text: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)

    def to_prompt_text(self) -> str:
        """Format screenshot reference for prompt inclusion."""
        handle = f"#{self.screenshot_id}" if self.screenshot_id is not None else "(no id)"
        try:
            time_str = datetime.fromtimestamp(self.timestamp).strftime("%H:%M:%S")
        except (OverflowError, OSError, ValueError, TypeError):
            time_str = "unknown time"
        preview = self.preview or self.description or "(no description yet)"
        lines = [f"[Screenshot {handle} · {time_str}] {preview}"]
        note = (self.description or "").strip()
        if note and note.lower() not in preview.lower():
            lines.append(f"  User note: {note}")
        if self.tier == "full":
            if self.ai_summary:
                lines.append(f"  Summary: {self.ai_summary}")
            if self.visible_text:
                lines.append(f"  Visible text: {' | '.join(self.visible_text)}")
            if self.keywords:
                lines.append(f"  Keywords: {', '.join(self.keywords)}")
        return "\n".join(lines)


def render_screenshot_section(screenshots: list[ScreenshotReference]) -> list[str]:
    """Prompt lines for the screenshots section; full-detail cards first."""
    if not screenshots:
        return []
    ordered = sorted(screenshots, key=lambda s: 0 if s.tier == "full" else 1)
    return [SCREENSHOT_SECTION_HEADER] + [s.to_prompt_text() for s in ordered] + [""]


@dataclass
class ConversationTurn:
    """Represents a single turn in the assistant conversation history."""
    role: str  # 'user' or 'assistant'
    content: str

    def to_prompt_text(self) -> str:
        """Format conversation turn for prompt inclusion."""
        role_label = "User" if self.role == "user" else "Assistant"
        return f"{role_label}: {self.content}"


@dataclass
class AssistantContext:
    """Complete context gathered for answering an assistant query."""
    query: str
    sessions: list[SessionCandidate] = field(default_factory=list)
    transcripts: list[TranscriptExcerpt] = field(default_factory=list)
    summaries: list[SummaryExcerpt] = field(default_factory=list)
    screenshots: list[ScreenshotReference] = field(default_factory=list)
    conversation_history: list[ConversationTurn] = field(default_factory=list)

    def to_prompt_text(self) -> str:
        """Render context as prompt text for LLM."""
        parts = []

        if self.conversation_history:
            parts.append("## Conversation History")
            for turn in self.conversation_history:
                parts.append(turn.to_prompt_text())
            parts.append("")

        if self.sessions:
            parts.append("## Relevant Sessions")
            for s in self.sessions:
                parts.append(s.to_prompt_text())
            parts.append("")

        if self.transcripts:
            parts.append("## Transcripts")
            for t in self.transcripts:
                parts.append(t.to_prompt_text())
            parts.append("")

        if self.summaries:
            parts.append("## Summaries")
            for s in self.summaries:
                parts.append(s.to_prompt_text())
            parts.append("")

        parts.extend(render_screenshot_section(self.screenshots))

        if not parts:
            return "(No context found)"

        return "\n".join(parts)

    def is_empty(self) -> bool:
        """Check if context has any content."""
        return (
            not self.sessions
            and not self.transcripts
            and not self.summaries
            and not self.screenshots
            and not self.conversation_history
        )