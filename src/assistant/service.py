"""Assistant answer service - coordinates session resolution, context retrieval, and answering."""

import logging
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .context_models import ScreenshotReference, TranscriptExcerpt, render_screenshot_section
from .openrouter_client import OpenRouterClient
from .rag_context_builder import build_any_session_context, build_routed_session_context
from .response_contract import RESPONSE_CONTRACT, parse_answer
from .scope_offer import ScopeOffer, build_scope_offer
from .live_qa import strip_transcript_evidence
from .screenshot_contract import SCREENSHOT_CONTRACT, parse_screenshot_refs
from .document_contract import DOCUMENT_CONTRACT, citation_line, parse_document_refs
from .session_documents import WHOLE, render_documents
from ..rag.router import route_sessions
from .session_resolver import (
    AssistantSessionResolver,
    ResolutionResult,
    ScopeResolution,
)
from .tools import AssistantRetrievalTools
from ..config import ANSWER_TEMPERATURE, ANY_SESSION_TEMPERATURE, ASSISTANT_AGENTS, get_selected_model
from ..storage.database import Database

logger = logging.getLogger(__name__)

# Most recent conversation messages replayed into the prompt. Older turns are
# dropped so a long back-and-forth never crowds out the retrieved context
# (ANY_SESSION_REFACTOR brief 2.4).
MAX_HISTORY_MESSAGES = 10

# Upper bound on one session summary in the Specific Session prompt. Real
# summaries run ~3k chars; this only guards against a runaway row.
MAX_SUMMARY_CHARS = 8000


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
    # Single dominant session to offer a direct scope switch for (BU092);
    # None whenever the field is ambiguous, so candidate_sessions still applies.
    scope_offer: Optional[ScopeOffer] = None
    # Screenshot ids the Specific Session answer points the user to (BU108).
    screenshot_refs: List[int] = None
    # Session documents the answer used (BU149): ``{"id", "name"}`` each.
    document_refs: List[Dict[str, Any]] = None

    def __post_init__(self):
        if self.document_refs is None:
            self.document_refs = []
        if self.screenshot_refs is None:
            self.screenshot_refs = []
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
        # Screenshot ids placed in the last Specific Session context (BU108);
        # the only ids an answer may cite.
        self._last_screenshot_ids: Set[int] = set()
        # Session documents placed in the last Specific Session context
        # (BU149): ``{"mode", "block", "names"}``; empty when it had none.
        self._last_documents: Dict[str, Any] = {}
        # Scope offers the user declined, per conversation (BU092). In-memory
        # only - not persisted, and cleared with the process.
        self._declined_offers: Dict[Optional[int], Set[int]] = {}

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
        self._last_screenshot_ids = set()
        self._last_documents = {}

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
        background, context_text, full_transcript = self._retrieve_context(
            needs_session_context, resolution, question, conversation_id
        )
        session_ids = resolution.session_ids
        is_any_session = resolution.scope == ScopeResolution.ALL_SESSIONS

        # Step 6: Build messages for OpenRouter. The whole transcript replaces
        # the earlier-transcript block a live answer question embeds (BU144).
        messages = self._build_messages(
            agent, context_text,
            strip_transcript_evidence(question) if full_transcript else question,
            conversation_id, is_any_session,
            background=background, full_transcript=full_transcript,
        )

        # Step 6: Call OpenRouter API
        try:
            # Use selected model from settings (don't pass explicit model)
            answer = self._call_openrouter(
                messages,
                temperature=ANY_SESSION_TEMPERATURE if is_any_session else ANSWER_TEMPERATURE,
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
        persist: bool = True,
        use_context: bool = True,
        model: Optional[str] = None,
        on_delta: Optional[Callable[[str], None]] = None,
        reasoning_effort: Optional[str] = None,
        max_tokens: Optional[int] = None,
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
            persist: False answers without saving a conversation (live Q&A
                answers in the Transcripts window are not chat history).
            use_context: False answers from general knowledge alone: no
                session context (transcript, summary, screenshots) is
                retrieved or sent, which is also the fastest path.
            model: Model for this answer; None uses the selected model.
            on_delta: Streams the answer: called with each text fragment from
                a worker thread. The finished response is still returned.
            reasoning_effort: Passed to reasoning models ("low" thinks less).
            max_tokens: Caps the answer length; setting it streams the answer.

        Returns:
            AnswerResponse with either:
                - success=True with answer on success
                - needs_clarification=True with clarification_question for ambiguous cases
                - success=False with error message on failure
        """
        # Reset per-request router state so a stale list never leaks into a
        # non-Any-Session response.
        self._last_routed_sessions = []
        self._last_screenshot_ids = set()
        self._last_documents = {}

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
        needs_session_context = use_context and self._needs_session_context(
            agent_id, question, resolution.scope)

        # Step 5: Retrieve context based on scope
        background, context_text, full_transcript = self._retrieve_context(
            needs_session_context, resolution, question, conversation_id
        )
        session_ids = resolution.session_ids
        is_any_session = resolution.scope == ScopeResolution.ALL_SESSIONS

        # Step 6: Build messages for OpenRouter. The whole transcript replaces
        # the earlier-transcript block a live answer question embeds (BU144).
        messages = self._build_messages(
            agent, context_text,
            strip_transcript_evidence(question) if full_transcript else question,
            conversation_id, is_any_session,
            background=background, full_transcript=full_transcript,
        )

        # Step 6: Call OpenRouter API
        try:
            # Use selected model from settings (don't pass explicit model)
            answer = await self._call_openrouter_async(
                messages,
                model=model,
                temperature=ANY_SESSION_TEMPERATURE if is_any_session else ANSWER_TEMPERATURE,
                on_delta=on_delta,
                reasoning_effort=reasoning_effort,
                max_tokens=max_tokens,
            )
        except Exception as e:
            return AnswerResponse(
                success=False,
                error=f"OpenRouter API call failed: {str(e)}",
            )

        return self._finalize_answer(
            answer, is_any_session, conversation_id, session_ids, question,
            persist=persist,
        )

    def _finalize_answer(
        self,
        raw_answer: str,
        is_any_session: bool,
        conversation_id: Optional[int],
        session_ids: List[int],
        question: str,
        persist: bool = True,
    ) -> AnswerResponse:
        """Strip the answer contract trailer, persist, and build the response.

        The metadata trailer is parsed and removed before persistence so the
        sentinel never re-enters the prompt as conversation history (brief 3.2).
        ``intent`` / ``evidence`` are internal - logged only, never rendered.
        """
        intent = evidence = None
        candidate_sessions: List[Dict[str, Any]] = []
        scope_offer: Optional[ScopeOffer] = None
        screenshot_refs: List[int] = []
        document_refs: List[Dict[str, Any]] = []
        stored_suffix = ""
        answer = raw_answer

        if not is_any_session and self._last_documents:
            # BU149: the documents line is the last one, so it comes off first
            # and the screenshot line is again among the last two.
            names = self._last_documents["names"]
            answer, cited = parse_document_refs(answer, names)
            document_refs = [{"id": i, "name": names[i]} for i in cited]
            if cited:
                stored_suffix = f"\n\n{citation_line(cited)}"
            logger.info("Specific Session answer documents: cited=%s", cited or "none")

        if not is_any_session and self._last_screenshot_ids:
            # BU108: validate the screenshot pointer line before persisting, so
            # a hallucinated id never reaches the chat or the history.
            answer, screenshot_refs = parse_screenshot_refs(
                answer, self._last_screenshot_ids
            )

        if is_any_session:
            parsed = parse_answer(raw_answer)
            answer = parsed.answer_text
            intent, evidence = parsed.intent, parsed.evidence
            candidate_sessions = list(self._last_routed_sessions)
            # BU092/BU093: when the answer points at a concrete meeting (the
            # model cited it, or it could only answer at a high level), offer a
            # direct switch for that session. candidate_sessions stays populated
            # so the prompt's "choose another session" path has the full list.
            scope_offer = build_scope_offer(
                self._last_routed_sessions,
                intent,
                evidence,
                self._declined_offers.get(conversation_id, ()),
                cited_session_ids=parsed.sessions,
            )
            logger.info(
                "Any Session answer contract: intent=%s evidence=%s sessions=%s offer=%s",
                intent, evidence, parsed.sessions,
                scope_offer.session_id if scope_offer else None,
            )

        conv_id = conversation_id
        if persist:
            try:
                conv_id = self._persist_conversation(
                    conversation_id,
                    session_ids[0] if session_ids else None,
                    question,
                    answer + stored_suffix,
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
            scope_offer=scope_offer,
            screenshot_refs=screenshot_refs,
            document_refs=document_refs,
        )

    def decline_scope_offer(
        self, conversation_id: Optional[int], session_id: int
    ) -> None:
        """Record that the user declined a scope offer for one session (BU092).

        Later turns of the same conversation will not offer that session again.
        A ``conversation_id`` of ``None`` (a brand-new conversation) is its own
        bucket and never collides with a real conversation's declines. Kept in
        memory only - no schema change, no persistence.
        """
        self._declined_offers.setdefault(conversation_id, set()).add(session_id)

    def forget_conversation(self, conversation_id: Optional[int]) -> None:
        """Drop every in-memory reference to a deleted conversation (BU096).

        Called after ``db.delete_conversation`` so a recycled conversation id
        cannot inherit stale state. No DB access; a no-op for an unknown id.
        """
        self._declined_offers.pop(conversation_id, None)

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

    def _retrieve_context(
        self,
        needs_session_context: bool,
        resolution: ResolutionResult,
        question: str,
        conversation_id: Optional[int],
    ) -> Tuple[str, str, str]:
        """Return ``(background, evidence, full_transcript)`` prompt text.

        ``background`` is the part that stays the same for every question in
        a Specific Session conversation (session name and summary); it goes
        into the system message so the provider can cache it. ``evidence`` is
        retrieved for this question and goes next to the question. Any Session
        context is routed per question, so it is all evidence.

        ``full_transcript`` (BU144) is set instead of ``background`` when the
        session is sent whole: the session header and the transcript, for the
        start of the system message. ``evidence`` then carries the summary,
        the transcript note and the screenshots.
        """
        if not needs_session_context:
            # research_helper general-knowledge question: history only.
            return "", "", ""
        if resolution.scope == ScopeResolution.ALL_SESSIONS:
            return "", self._get_all_sessions_context(question), ""
        if resolution.session_ids:
            return self._get_single_session_context(
                resolution.session_ids[0], question, conversation_id
            )
        return "", "", ""

    def _get_single_session_context(
        self, session_id: int, question: str, conversation_id: Optional[int] = None
    ) -> Tuple[str, str, str]:
        """Get context for a single session as
        ``(background, evidence, full_transcript)``."""
        try:
            context = self._tools.get_session_context(
                session_id, question, conversation_id=conversation_id
            )
            if "error" in context:
                return "", f"Error retrieving context: {context['error']}", ""

            self._last_screenshot_ids = {
                sc["screenshot_id"]
                for sc in context.get("screenshots", [])
                if sc.get("screenshot_id") is not None
            }

            state = context.get("transcript_state") or {}
            full = bool(state.get("full"))
            logger.info(
                "Specific Session answer: mode=%s transcript_chars=%d",
                "full" if full else "search",
                state.get("chars", 0) if full
                else sum(len(t.get("text") or "") for t in context.get("transcripts", [])),
            )
            full_transcript = self._full_transcript_prompt(context) if full else ""
            background = "" if full else self._session_background_prompt(context)
            evidence = self._question_evidence_prompt(context)
            if not background and not evidence and not full_transcript:
                evidence = "(No context found)"
            self._load_session_documents(session_id, question)
            return background, evidence, full_transcript
        except Exception as e:
            return "", f"Context retrieval failed: {str(e)}", ""

    def _load_session_documents(self, session_id: int, question: str) -> None:
        """Put the session's documents in ``_last_documents`` (BU149).

        Specific Session only. With no documents nothing is stored, so the
        prompt is exactly what it was before documents existed. A failure to
        read them costs the documents, never the answer.
        """
        try:
            documents = self._db.get_session_documents(session_id)
            if not isinstance(documents, list) or not documents:
                return
            mode, block = render_documents(documents, question)
        except Exception as e:
            logger.warning("Session documents unavailable for session %s: %s", session_id, e)
            return
        self._last_documents = {
            "mode": mode,
            "block": block,
            "names": {d["id"]: d["name"] for d in documents},
        }
        logger.info(
            "Specific Session documents: count=%d mode=%s chars=%d",
            len(documents), mode, len(block),
        )

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
                    parts.append(f"[{s.get('name', 'Untitled')}{start_time_str}]")
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

    def _session_background_prompt(self, context: Dict[str, Any]) -> str:
        """Session name and summary: identical for every question in a session.

        Kept apart from the per-question evidence so it can sit at the start of
        the prompt, where the provider's prompt cache can reuse it.
        """
        # Conversation history is deliberately not rendered here: _build_messages
        # already replays it as real chat turns, and rendering it again doubled
        # the history tokens on every follow-up question.
        return "\n".join(
            self._session_header_lines(context) + self._summary_lines(context)
        )

    @staticmethod
    def _session_header_lines(context: Dict[str, Any]) -> List[str]:
        parts = []
        if context.get("sessions"):
            parts.append("## Relevant Sessions")
            for s in context["sessions"]:
                parts.append(f"[Session {s['session_id']}] {s.get('session_name', 'Unknown')}")
            parts.append("")
        return parts

    @staticmethod
    def _summary_lines(context: Dict[str, Any]) -> List[str]:
        parts = []
        if context.get("summaries"):
            # Full text, so the Due Dates section reaches the model (a 500-char
            # cut stopped inside the Overview). Regenerating a summary appends a
            # new row, so keep only the latest per type (rows arrive oldest first).
            latest = {}
            for s in context["summaries"]:
                latest[s.get("summary_type", "summary")] = s.get("content", "")
            parts.append("## Summaries")
            for summary_type, content in latest.items():
                parts.append(f"[{summary_type}]: {content[:MAX_SUMMARY_CHARS]}")
            parts.append("")
        return parts

    def _full_transcript_prompt(self, context: Dict[str, Any]) -> str:
        """Session header and the whole transcript, in time order (BU144).

        Ends the system message, so while a session records only lines
        appended at its end change and the provider reuses the earlier prefix.
        """
        known = {f.name for f in fields(TranscriptExcerpt)}
        lines = [
            TranscriptExcerpt(**{k: v for k, v in t.items() if k in known}).to_prompt_text()
            for t in sorted(context["transcripts"], key=lambda t: t.get("timestamp") or 0)
        ]
        return "\n".join(
            self._session_header_lines(context)
            + ["## Full transcript (in time order)"] + lines
        )

    def _question_evidence_prompt(self, context: Dict[str, Any]) -> str:
        """Transcript excerpts and screenshots retrieved for this question."""
        parts = []

        note = self._transcript_state_note(
            context.get("transcript_state") or {}, bool(context.get("summaries"))
        )
        if note:
            parts.extend([note, ""])

        full = bool((context.get("transcript_state") or {}).get("full"))
        if full:
            # The transcript is in the system message; the summary changes
            # later, so it rides with the question (BU144).
            parts.extend(self._summary_lines(context))

        if context.get("transcripts") and not full:
            # BU142: time order with HH:MM:SS, so "at the end of the class" and
            # timestamp citations work. Excerpts are already length-bounded by
            # the retriever; the session name is in the background block.
            parts.append("## Transcripts (in time order)")
            known = {f.name for f in fields(TranscriptExcerpt)}
            for t in sorted(context["transcripts"], key=lambda t: t.get("timestamp") or 0):
                parts.append(
                    TranscriptExcerpt(**{k: v for k, v in t.items() if k in known}).to_prompt_text()
                )
            parts.append("")

        if context.get("screenshots"):
            known = {f.name for f in fields(ScreenshotReference)}
            parts.extend(render_screenshot_section([
                ScreenshotReference(**{k: v for k, v in sc.items() if k in known})
                for sc in context["screenshots"]
            ]))

        return "\n".join(parts)

    @staticmethod
    def _transcript_state_note(state: Dict[str, Any], has_summary: bool) -> str:
        """Tell the model the transcript is still growing (BU142).

        Empty for a finished, indexed session. Otherwise the model learns the
        transcript stops at a given time and may have no summary yet, so it
        does not read missing content as "never said".
        """
        live = state.get("live", False)
        if not live and state.get("indexed", True):
            return ""
        until = state.get("until") or 0
        until_text = (
            f" up to {datetime.fromtimestamp(until).strftime('%H:%M:%S')}" if until else ""
        )
        if live:
            note = (f"Note: this session is still being recorded. The transcript runs"
                    f"{until_text}; anything said after that is not in it yet.")
        else:
            note = (f"Note: this session was just stopped and is still being processed."
                    f" The transcript runs{until_text}.")
        if not has_summary:
            note += " There is no summary yet, so answer from the transcript excerpts."
        return note

    def _build_messages(
        self,
        agent: Dict[str, Any],
        context: str,
        question: str,
        conversation_id: Optional[int],
        is_any_session: bool = False,
        background: str = "",
        full_transcript: str = "",
    ) -> List[Dict[str, str]]:
        """Build OpenRouter messages, with the text that repeats across a
        conversation's questions first.

        Providers such as Gemini bill a prompt prefix they have seen recently
        at a fraction of the input price, but only up to the first character
        that differs. So the system message holds only what stays the same for
        every question (instructions, session ``background``, answer contract),
        the history follows, and ``context`` - retrieved for this question -
        is sent last, together with the question.
        """
        messages = []

        # Session documents (BU149) are Specific Session only; Any Session
        # never reads them.
        documents = {} if is_any_session else self._last_documents

        system_content = agent['system_instruction']
        if documents and not full_transcript:
            system_content = self._with_document_prompt(system_content, documents)
        if background:
            system_content = f"{system_content}\n\nContext:\n{background}"
        if full_transcript:
            # BU144: instruction, screenshot contract, then the transcript last,
            # so a growing transcript only extends the end of the prefix. The
            # documents sit before the transcript for the same reason.
            if self._last_screenshot_ids:
                system_content = f"{system_content}\n\n{SCREENSHOT_CONTRACT}"
            if documents:
                system_content = self._with_document_prompt(system_content, documents)
            system_content = f"{system_content}\n\nContext:\n{full_transcript}"
        # Any Session mode only: ask the model to self-classify its answer via
        # the metadata trailer. Specific Session prompts are left untouched.
        if is_any_session:
            system_content = f"{system_content}\n\n{RESPONSE_CONTRACT}"
        elif self._last_screenshot_ids and not full_transcript:
            # Specific Session with screenshots in context: let the model point
            # the user at the screenshot that holds the answer (BU108). Every
            # question of a session that has screenshots lists them, so this
            # does not change between questions.
            system_content = f"{system_content}\n\n{SCREENSHOT_CONTRACT}"
        messages.append({"role": "system", "content": system_content})

        # Add conversation history if continuing, capped to the most recent
        # turns so it cannot crowd out the retrieved context.
        if conversation_id:
            try:
                history = self._db.get_messages(conversation_id)[-MAX_HISTORY_MESSAGES:]
                for msg in history:
                    messages.append({
                        "role": msg["role"],
                        "content": msg["content"],
                    })
            except Exception:
                # Ignore history errors - start fresh
                pass

        # Current question, preceded by the context retrieved for it. Only the
        # bare question is persisted, so history never carries old context.
        if documents and documents["mode"] != WHOLE:
            # Excerpts depend on the question, so they ride with it.
            context = f"{context}\n\n{documents['block']}" if context else documents["block"]
        user_content = question
        if context:
            user_content = f"Context for this question:\n{context}\n\nQuestion: {question}"
        messages.append({"role": "user", "content": user_content})

        return messages

    @staticmethod
    def _with_document_prompt(system_content: str, documents: Dict[str, Any]) -> str:
        """``system_content`` plus the document contract, and the documents
        themselves when they are sent whole (the same for every question)."""
        system_content = f"{system_content}\n\n{DOCUMENT_CONTRACT}"
        if documents["mode"] == WHOLE:
            system_content = f"{system_content}\n\n{documents['block']}"
        return system_content

    def _call_openrouter(
        self,
        messages: List[Dict[str, str]],
        model: str = None,
        temperature: Optional[float] = None,
    ) -> str:
        """Call OpenRouter API and return the answer.

        ``temperature=None`` leaves the client at its default (0.7). The answer
        paths pass ``ANY_SESSION_TEMPERATURE`` (reliable contract compliance)
        or ``ANSWER_TEMPERATURE`` (stay on the evidence).
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
        on_delta: Optional[Callable[[str], None]] = None,
        reasoning_effort: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Call OpenRouter API and return the answer (see _call_openrouter).

        With ``on_delta``, ``reasoning_effort`` or ``max_tokens`` the answer
        is streamed.
        """
        # Always use fresh selected model - don't cache the client with a fixed model
        # This ensures model changes in settings are immediately applied
        selected_model = get_selected_model()
        effective_model = model or selected_model

        # Create new client each time to ensure fresh model selection
        openrouter_client = OpenRouterClient(
            model=effective_model, temperature=temperature
        )

        if on_delta is not None or reasoning_effort or max_tokens:
            return await openrouter_client.chat_stream_async(
                messages, on_delta=on_delta, reasoning_effort=reasoning_effort,
                max_tokens=max_tokens,
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
