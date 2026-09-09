"""Assistant answer service - coordinates session resolution, context retrieval, and answering."""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .openrouter_client import OpenRouterClient
from .rag_context_builder import build_any_session_context, build_routed_session_context
from .response_contract import RESPONSE_CONTRACT, parse_answer
from ..rag.router import route_sessions
from .session_resolver import (
    AssistantSessionResolver,
    ResolutionResult,
    ScopeResolution,
)
from .tools import AssistantRetrievalTools
from ..config import ANY_SESSION_TEMPERATURE, ASSISTANT_AGENTS, get_selected_model
from ..storage.database import Database

logger = logging.getLogger(__name__)


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
    # Router-selected sessions for Any Session answers (BU089); empty otherwise.
    routed_sessions: List[Dict[str, Any]] = None
    # Answer contract signal (BU090). Internal only - never rendered, logged only.
    intent: Optional[str] = None
    evidence: Optional[str] = None
    # "any_session" or "current_session"; which scope produced this answer.
    scope_used: Optional[str] = None
    # Routed sessions to seed the Specific Session handoff picker (BU090).
    candidate_sessions: List[Dict[str, Any]] = None

    def __post_init__(self):
        if self.candidates is None:
            self.candidates = []
        if self.routed_sessions is None:
            self.routed_sessions = []
        if self.candidate_sessions is None:
            self.candidate_sessions = []


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
        # Populated by _get_all_sessions_context; surfaced on AnswerResponse.
        self._last_routed_sessions: List[Dict[str, Any]] = []

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
                - success=True with answer on success
                - needs_clarification=True with clarification_question for ambiguous cases
                - success=False with error message on failure
        """
        # Reset per-request router state so a stale list never leaks into a
        # non-Any-Session response.
        self._last_routed_sessions = []

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
                clarification_question="Please set the scope to a specific session.",
                error="No session could be inferred from the question or context.",
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

        # Step 4: Determine if question requires session context
        # Research Helper can answer general knowledge questions without session context
        needs_session_context = self._needs_session_context(agent_id, question, resolution.scope)

        # Step 5: Retrieve context based on scope
        context_text = ""
        session_ids = resolution.session_ids
        is_any_session = resolution.scope == ScopeResolution.ALL_SESSIONS

        if needs_session_context:
            if is_any_session:
                # Cross-session: use search tools
                context_text = self._get_all_sessions_context(question)
            else:
                # Single session: use bounded context
                if session_ids:
                    context_text = self._get_single_session_context(
                        session_ids[0], question, conversation_id
                    )
        else:
            # For research_helper with general knowledge questions, use minimal context
            # Just include conversation history, no session data needed
            context_text = ""

        # Step 6: Build messages for OpenRouter
        messages = self._build_messages(
            agent, context_text, question, conversation_id, is_any_session
        )

        # Step 6: Call OpenRouter API
        try:
            # Use selected model from settings (don't pass explicit model)
            answer = self._call_openrouter(
                messages,
                temperature=ANY_SESSION_TEMPERATURE if is_any_session else None,
            )
        except Exception as e:
            return AnswerResponse(
                success=False,
                error=f"OpenRouter API call failed: {str(e)}",
            )

        # Step 6b: In Any Session mode, split the answer contract trailer off the
        # raw response BEFORE persistence so the sentinel never re-enters the
        # prompt as conversation history on later turns.
        return self._finalize_answer(
            answer, is_any_session, conversation_id, session_ids, question
        )

    async def ask_async(
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
                - success=True with answer on success
                - needs_clarification=True with clarification_question for ambiguous cases
                - success=False with error message on failure
        """
        # Reset per-request router state so a stale list never leaks into a
        # non-Any-Session response.
        self._last_routed_sessions = []

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
                clarification_question="Please set the scope to a specific session.",
                error="No session could be inferred from the question or context.",
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

        # Step 4: Determine if question requires session context
        # Research Helper can answer general knowledge questions without session context
        needs_session_context = self._needs_session_context(agent_id, question, resolution.scope)

        # Step 5: Retrieve context based on scope
        context_text = ""
        session_ids = resolution.session_ids
        is_any_session = resolution.scope == ScopeResolution.ALL_SESSIONS

        if needs_session_context:
            if is_any_session:
                # Cross-session: use search tools
                context_text = self._get_all_sessions_context(question)
            else:
                # Single session: use bounded context
                if session_ids:
                    context_text = self._get_single_session_context(
                        session_ids[0], question, conversation_id
                    )
        else:
            # For research_helper with general knowledge questions, use minimal context
            # Just include conversation history, no session data needed
            context_text = ""

        # Step 6: Build messages for OpenRouter
        messages = self._build_messages(
            agent, context_text, question, conversation_id, is_any_session
        )

        # Step 6: Call OpenRouter API
        try:
            # Use selected model from settings (don't pass explicit model)
            answer = await self._call_openrouter_async(
                messages,
                temperature=ANY_SESSION_TEMPERATURE if is_any_session else None,
            )
        except Exception as e:
            return AnswerResponse(
                success=False,
                error=f"OpenRouter API call failed: {str(e)}",
            )

        return self._finalize_answer(
            answer, is_any_session, conversation_id, session_ids, question
        )

    def _finalize_answer(
        self,
        raw_answer: str,
        is_any_session: bool,
        conversation_id: Optional[int],
        session_ids: List[int],
        question: str,
    ) -> AnswerResponse:
        """Strip the answer contract trailer, persist, and build the response.

        The metadata trailer is parsed and removed before persistence so the
        sentinel never re-enters the prompt as conversation history (brief 3.2).
        ``intent`` / ``evidence`` are internal - logged only, never rendered.
        """
        intent = evidence = None
        candidate_sessions: List[Dict[str, Any]] = []
        answer = raw_answer

        if is_any_session:
            parsed = parse_answer(raw_answer)
            answer = parsed.answer_text
            intent, evidence = parsed.intent, parsed.evidence
            candidate_sessions = list(self._last_routed_sessions)
            logger.info(
                "Any Session answer contract: intent=%s evidence=%s sessions=%s",
                intent, evidence, parsed.sessions,
            )

        try:
            conv_id = self._persist_conversation(
                conversation_id,
                session_ids[0] if session_ids else None,
                question,
                answer,
            )
        except Exception:
            # Don't fail the answer if persistence fails - just log
            conv_id = conversation_id

        return AnswerResponse(
            success=True,
            answer=answer,
            conversation_id=conv_id,
            routed_sessions=list(self._last_routed_sessions),
            intent=intent,
            evidence=evidence,
            scope_used="any_session" if is_any_session else "current_session",
            candidate_sessions=candidate_sessions,
        )

    def _get_agent_config(self, agent_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """Get agent configuration by ID."""
        if agent_id is None:
            agent_id = ASSISTANT_AGENTS.get("default", "chronicle_assistant")
        return self._agents.get(agent_id)

    def _needs_session_context(
        self,
        agent_id: str,
        question: str,
        scope: ScopeResolution,
    ) -> bool:
        """
        Determine if a question requires session/transcript context.
        
        For research_helper, questions NOT about meetings can be answered
        with general knowledge. For other agents, always require context.
        """
        # Non-research agents always need session context
        if agent_id != "research_helper":
            return True
        
        # Research helper: check if scope indicates session interest
        # If user explicitly selected a session or asked about sessions, use context
        if scope in (ScopeResolution.CURRENT_SESSION, ScopeResolution.SELECTED_SESSION):
            return True
        
        # For ALL_SESSIONS or ambiguous cases, also use context to search transcripts
        if scope == ScopeResolution.ALL_SESSIONS:
            return True
        
        # For research_helper with no specific session context requested,
        # we allow general knowledge questions
        # But still include any available context from conversation history
        return False

    def _get_single_session_context(
        self, session_id: int, question: str, conversation_id: Optional[int] = None
    ) -> str:
        """Get bounded context for a single session."""
        try:
            context = self._tools.get_session_context(
                session_id, question, conversation_id=conversation_id
            )
            if "error" in context:
                return f"Error retrieving context: {context['error']}"
            
            # Convert to prompt text
            return self._dict_to_context_prompt(context)
        except Exception as e:
            return f"Context retrieval failed: {str(e)}"

    def _get_all_sessions_context(self, question: str) -> str:
        """Route to the relevant sessions, then build context from those only.

        Two-tier (BU089): Tier-1 picks the handful of sessions the question is
        about; Tier-2 chunk search runs only inside them. Falls back to the
        legacy unified/keyword search when routing yields nothing.
        """
        routed = []
        try:
            routed = route_sessions(self._db, question, k=5)
        except Exception:
            routed = []

        self._last_routed_sessions = [
            {
                "session_id": r.session_id,
                "session_name": r.name,
                "start_time": r.start_time,
                "score": r.score,
                "reason": r.reason,
            }
            for r in routed
        ]

        if routed:
            try:
                context = build_routed_session_context(self._db, question, routed)
                if context and not context.startswith("(No relevant context"):
                    return context
            except Exception:
                pass

        # Fall back: unified RAG search, then legacy keyword search.
        try:
            results = self._tools.search_everything(question, limit=30)
            if results and "error" not in results[0]:
                return build_any_session_context(question, results)
        except Exception:
            pass

        return self._get_all_sessions_context_legacy(question)

    def _get_all_sessions_context_legacy(self, question: str) -> str:
        """Get cross-session context using separate search tools (legacy fallback)."""
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

        # Add conversation history first (most relevant to current conversation)
        if context.get("conversation_history"):
            parts.append("## Conversation History")
            for turn in context["conversation_history"]:
                role_label = "User" if turn.get("role") == "user" else "Assistant"
                parts.append(f"{role_label}: {turn.get('content', '')}")
            parts.append("")

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
        is_any_session: bool = False,
    ) -> List[Dict[str, str]]:
        """Build OpenRouter messages with system prompt, context, and history."""
        messages = []

        # System message with context
        system_content = f"{agent['system_instruction']}\n\nContext:\n{context}"
        # Any Session mode only: ask the model to self-classify its answer via
        # the metadata trailer. Specific Session prompts are left untouched.
        if is_any_session:
            system_content = f"{system_content}\n\n{RESPONSE_CONTRACT}"
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

    def _call_openrouter(
        self,
        messages: List[Dict[str, str]],
        model: str = None,
        temperature: Optional[float] = None,
    ) -> str:
        """Call OpenRouter API and return the answer.

        ``temperature=None`` leaves the client at its default (0.7); the Any
        Session path passes ``ANY_SESSION_TEMPERATURE`` for reliable contract
        compliance. Other call paths are unchanged.
        """
        # Always use fresh selected model - don't cache the client with a fixed model
        # This ensures model changes in settings are immediately applied
        selected_model = get_selected_model()
        effective_model = model or selected_model

        # Create new client each time to ensure fresh model selection
        openrouter_client = OpenRouterClient(
            model=effective_model, temperature=temperature
        )

        return openrouter_client.chat(messages)

    async def _call_openrouter_async(
        self,
        messages: List[Dict[str, str]],
        model: str = None,
        temperature: Optional[float] = None,
    ) -> str:
        """Call OpenRouter API and return the answer (see _call_openrouter)."""
        # Always use fresh selected model - don't cache the client with a fixed model
        # This ensures model changes in settings are immediately applied
        selected_model = get_selected_model()
        effective_model = model or selected_model

        # Create new client each time to ensure fresh model selection
        openrouter_client = OpenRouterClient(
            model=effective_model, temperature=temperature
        )

        return await openrouter_client.chat_async(messages)

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
