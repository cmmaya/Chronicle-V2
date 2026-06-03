"""Assistant answer service - coordinates session resolution, context retrieval, and answering."""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .openrouter_client import OpenRouterClient
from .session_resolver import (
    AssistantSessionResolver,
    ResolutionResult,
    ScopeResolution,
)
from .tools import AssistantRetrievalTools
from ..config import ASSISTANT_AGENTS
from ..storage.database import Database


@dataclass
class AnswerResponse:
    """Structured response from the answer service."""
    success: bool
    answer: Optional[str] = None
    needs_clarification: bool = False
    clarification_question: Optional[str] = None
    candidates: List[Dict[str, Any]] = None
    conversation_id: Optional[int] = None
    error: Optional[str] = None

    def __post_init__(self):
        if self.candidates is None:
            self.candidates = []


class AssistantAnswerService:
    """
    Coordinates session resolution, context retrieval, OpenRouter answering,
    and conversation persistence.
    
    Provides a single stable entrypoint (ask) for UI wiring without exposing
    retrieval, ambiguity handling, or OpenRouter internals.
    """

    def __init__(
        self,
        db: Database,
        openrouter_client: Optional[OpenRouterClient] = None,
    ):
        """
        Initialize the answer service.

        Args:
            db: Database instance for conversation persistence and retrieval.
            openrouter_client: Optional OpenRouter client (created if not provided).
        """
        self._db = db
        self._resolver = AssistantSessionResolver(db)
        self._tools = AssistantRetrievalTools(db)
        self._openrouter_client = openrouter_client
        self._agents = ASSISTANT_AGENTS.get("agents", {})

    def ask(
        self,
        question: str,
        agent_id: Optional[str] = None,
        explicit_scope: Optional[str] = None,
        active_session_id: Optional[int] = None,
        selected_session_id: Optional[int] = None,
        conversation_id: Optional[int] = None,
    ) -> AnswerResponse:
        """
        Process a user question and return an answer.

        Args:
            question: The user's question.
            agent_id: Agent config ID to use (defaults to configured default).
            explicit_scope: Explicit scope hint - "current_session", "any_session", or None.
            active_session_id: Currently active session ID, if any.
            selected_session_id: Currently selected session ID in UI, if any.
            conversation_id: Existing conversation ID to continue, or None for new.

        Returns:
            AnswerResponse with either:
            - success=True and answer text
            - needs_clarification=True with clarification_question for ambiguous cases
            - error message on failure
        """
        # Step 1: Resolve session scope
        resolution = self._resolver.resolve(
            question=question,
            active_session_id=active_session_id,
            selected_session_id=selected_session_id,
            explicit_scope=explicit_scope,
        )

        # Step 2: Handle ambiguous cases - return clarification without calling OpenRouter
        if resolution.scope == ScopeResolution.NEEDS_CLARIFICATION:
            return AnswerResponse(
                success=False,
                needs_clarification=True,
                clarification_question="Which session would you like me to search?",
                error="No session could be inferred from the question or context.",
            )

        if resolution.scope == ScopeResolution.AMBIGUOUS:
            candidates = [
                {
                    "session_id": c.session_id,
                    "session_name": c.session_name,
                    "start_time": c.start_timestamp,
                }
                for c in resolution.candidates
            ]
            candidate_list = "\n".join(
                f"- {c['session_name']} (Session {c['session_id']})"
                for c in candidates
            )
            return AnswerResponse(
                success=False,
                needs_clarification=True,
                clarification_question=(
                    f"I found multiple matching sessions. Which one would you like me to use?\n"
                    f"{candidate_list}"
                ),
                candidates=candidates,
                error="Ambiguous session scope - user clarification required.",
            )

        # Step 3: Get agent config
        agent = self._get_agent_config(agent_id)
        if agent is None:
            return AnswerResponse(
                success=False,
                error=f"Agent '{agent_id}' not found.",
            )

        # Step 4: Retrieve context based on scope
        context_text = ""
        session_ids = resolution.session_ids

        if resolution.scope == ScopeResolution.ALL_SESSIONS:
            # Cross-session: use search tools
            context_text = self._get_all_sessions_context(question)
        else:
            # Single session: use bounded context
            if session_ids:
                context_text = self._get_single_session_context(
                    session_ids[0], question
                )

        # Step 5: Build messages for OpenRouter
        messages = self._build_messages(agent, context_text, question, conversation_id)

        # Step 6: Call OpenRouter API
        try:
            answer = self._call_openrouter(messages, agent["model"])
        except Exception as e:
            return AnswerResponse(
                success=False,
                error=f"OpenRouter API call failed: {str(e)}",
            )

        # Step 7: Persist conversation
        try:
            conv_id = self._persist_conversation(
                conversation_id,
                session_ids[0] if session_ids else None,
                question,
                answer,
            )
        except Exception as e:
            # Don't fail the answer if persistence fails - just log
            conv_id = conversation_id

        return AnswerResponse(
            success=True,
            answer=answer,
            conversation_id=conv_id,
        )

    def _get_agent_config(self, agent_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """Get agent configuration by ID."""
        if agent_id is None:
            agent_id = ASSISTANT_AGENTS.get("default", "chronicle_assistant")
        return self._agents.get(agent_id)

    def _get_single_session_context(self, session_id: int, question: str) -> str:
        """Get bounded context for a single session."""
        try:
            context = self._tools.get_session_context(session_id, question)
            if "error" in context:
                return f"Error retrieving context: {context['error']}"
            
            # Convert to prompt text
            return self._dict_to_context_prompt(context)
        except Exception as e:
            return f"Context retrieval failed: {str(e)}"

    def _get_all_sessions_context(self, question: str) -> str:
        """Get cross-session context using search tools."""
        parts = []

        # First, always include the list of all sessions
        try:
            sessions = self._tools.list_sessions(limit=50)
            if sessions and "error" not in sessions[0]:
                parts.append("## Sessions")
                for s in sessions:
                    start_time_str = ""
                    if s.get('start_time'):
                        try:
                            from datetime import datetime
                            dt = datetime.fromtimestamp(s['start_time'])
                            start_time_str = f" ({dt.strftime('%Y-%m-%d %H:%M')})"
                        except:
                            pass
                    trans_status = s.get('transcription_status', 'none')
                    sum_status = s.get('summary_status', 'none')
                    parts.append(f"[{s.get('name', 'Untitled')}{start_time_str}] - Transcribed: {trans_status}, Summarized: {sum_status}")
                parts.append("")
        except Exception:
            pass

        # Search summaries
        try:
            summaries = self._tools.search_summaries(question, limit=5)
            if summaries and "error" not in summaries[0]:
                parts.append("## Relevant Summaries")
                for s in summaries:
                    if "session_name" in s:
                        parts.append(f"[{s['session_name']}]: {s.get('content', '')[:500]}")
                parts.append("")
        except Exception:
            pass

        # Search transcripts
        try:
            transcripts = self._tools.search_transcripts(question, limit=10)
            if transcripts and "error" not in transcripts[0]:
                parts.append("## Relevant Transcripts")
                for t in transcripts:
                    session_name = t.get("session_name", f"Session {t.get('session_id')}")
                    parts.append(f"[{session_name}]: {t.get('text', '')[:300]}")
                parts.append("")
        except Exception:
            pass

        if not parts:
            return "(No relevant context found in any session)"

        return "\n".join(parts)

    def _dict_to_context_prompt(self, context: Dict[str, Any]) -> str:
        """Convert context dict to prompt-friendly text."""
        parts = []

        if context.get("sessions"):
            parts.append("## Relevant Sessions")
            for s in context["sessions"]:
                parts.append(f"[Session {s['session_id']}] {s.get('session_name', 'Unknown')}")
            parts.append("")

        if context.get("summaries"):
            parts.append("## Summaries")
            for s in context["summaries"]:
                parts.append(f"[{s.get('summary_type', 'summary')}]: {s.get('content', '')[:500]}")
            parts.append("")

        if context.get("transcripts"):
            parts.append("## Transcripts")
            for t in context["transcripts"]:
                parts.append(f"[{t.get('session_name', 'Session')}] ({t.get('source', 'mic')}): {t.get('text', '')[:300]}")
            parts.append("")

        if context.get("screenshots"):
            parts.append("## Screenshots")
            for sc in context["screenshots"]:
                parts.append(f"[Screenshot at {sc.get('timestamp', 0)}]: {sc.get('filepath', '')}")
            parts.append("")

        if not parts:
            return "(No context found)"

        return "\n".join(parts)

    def _build_messages(
        self,
        agent: Dict[str, Any],
        context: str,
        question: str,
        conversation_id: Optional[int],
    ) -> List[Dict[str, str]]:
        """Build OpenRouter messages with system prompt, context, and history."""
        messages = []

        # System message with context
        system_content = f"{agent['system_instruction']}\n\nContext:\n{context}"
        messages.append({"role": "system", "content": system_content})

        # Add conversation history if continuing
        if conversation_id:
            try:
                history = self._db.get_messages(conversation_id)
                for msg in history:
                    messages.append({
                        "role": msg["role"],
                        "content": msg["content"],
                    })
            except Exception:
                # Ignore history errors - start fresh
                pass

        # Add current question
        messages.append({"role": "user", "content": question})

        return messages

    def _call_openrouter(self, messages: List[Dict[str, str]], model: str) -> str:
        """Call OpenRouter API and return the answer."""
        if self._openrouter_client is None:
            self._openrouter_client = OpenRouterClient(model=model)

        return self._openrouter_client.chat(messages, model=model)

    def _persist_conversation(
        self,
        conversation_id: Optional[int],
        session_id: Optional[int],
        question: str,
        answer: str,
    ) -> int:
        """Persist user question and assistant answer to database."""
        # Create new conversation if needed
        if conversation_id is None:
            conversation_id = self._db.create_conversation(
                session_id=session_id,
                title=question[:50] if question else "New conversation",
            )

        # Add user question
        self._db.add_message(conversation_id, "user", question)

        # Add assistant answer
        self._db.add_message(conversation_id, "assistant", answer)

        return conversation_id
