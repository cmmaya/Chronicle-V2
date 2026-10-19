"""Single-session scope switch offer for Any Session answers (BU092 / BU093).

When an Any Session answer points at a concrete meeting - the model cited it,
or it could only answer at a high level - the app offers a one-click switch to
Specific Session for that meeting (the in-chat prompt is BU093). This module
holds the decision: given the routed sessions and the answer-contract signals,
name the session worth offering, or return ``None``.

The prompt always also offers a "choose another session" path (the BU090
candidate picker), so this function never has to resolve ambiguity itself - it
just picks the best single guess.

Logic only. Pure and total: no database access, and no exception on malformed
or partial routed-session dicts.

See docs/build_plan/ANY_SESSION_REFACTOR.md sections 2.6, 3.3 and decision 9.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional

from .response_contract import should_hand_off


@dataclass
class ScopeOffer:
    """One concrete meeting the user can switch to Specific Session for."""

    session_id: int
    session_name: str
    start_time: Optional[int] = None


def _pick_target(
    routed_sessions: List[Any],
    cited_session_ids: Optional[Iterable[int]],
) -> Optional[Dict[str, Any]]:
    """The routed session the answer is about: the first cited session that was
    routed, else the top routed session. ``None`` if neither is usable.
    """
    if cited_session_ids:
        by_id = {
            s.get("session_id"): s
            for s in routed_sessions
            if isinstance(s, dict) and s.get("session_id") is not None
        }
        for sid in cited_session_ids:
            if sid in by_id:
                return by_id[sid]

    top = routed_sessions[0]
    return top if isinstance(top, dict) else None


def build_scope_offer(
    routed_sessions: Optional[List[Dict[str, Any]]],
    intent: Optional[str],
    evidence: Optional[str],
    declined_session_ids: Optional[Iterable[int]] = None,
    cited_session_ids: Optional[Iterable[int]] = None,
) -> Optional[ScopeOffer]:
    """Decide which one meeting to offer a Specific Session switch for.

    An offer is returned when all of these hold:
      - the answer is not "in no session" (``evidence != "none"``);
      - there is at least one routed session;
      - the answer either points at a concrete session (``cited_session_ids``,
        from the answer-contract trailer) or could not reach detail
        (``should_hand_off``);
      - that session has not already been declined in this conversation.

    The session offered is the first cited session that was routed, falling back
    to the top routed session. Ambiguity is not resolved here - the caller also
    offers a "choose another session" path.
    """
    if evidence == "none":
        return None

    if not routed_sessions:
        return None

    if not (should_hand_off(intent, evidence) or cited_session_ids):
        return None

    target = _pick_target(routed_sessions, cited_session_ids)
    if not isinstance(target, dict):
        return None

    session_id = target.get("session_id")
    if session_id is None:
        return None

    if declined_session_ids and session_id in set(declined_session_ids):
        # The user already said no to this session for this conversation.
        return None

    session_name = target.get("session_name") or f"Session {session_id}"

    return ScopeOffer(
        session_id=session_id,
        session_name=session_name,
        start_time=target.get("start_time"),
    )


# --- BU093: in-chat prompt text and choice plumbing ----------------------
#
# The prompt widget itself performs no application logic (BU093). These pure
# helpers hold the two things worth testing without a Qt harness: the exact
# sentence, and the shape of the accept / decline calls.


def _format_offer_timestamp(start_time: Any) -> Optional[str]:
    """``YYYY-MM-DD HH:MM`` for a unix timestamp, matching the format the BU090
    candidate list already renders. ``None`` for a missing or unparseable value.
    """
    if not start_time:
        return None
    try:
        return datetime.fromtimestamp(start_time).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def format_scope_offer_prompt(session_name: str, start_time: Any = None) -> str:
    """The exact sentence shown in the in-chat scope switch prompt (BU093).

    A missing or unparseable ``start_time`` drops the date and its comma rather
    than printing a placeholder.
    """
    when = _format_offer_timestamp(start_time)
    if when:
        return (
            "Would you like to change the scope to Specific for session: "
            f"{session_name}, {when}?"
        )
    return (
        "Would you like to change the scope to Specific for session: "
        f"{session_name}?"
    )


def accept_scope_offer(
    offer: ScopeOffer,
    question: str,
    agent_id: Optional[str],
    run_query: Callable[..., Any],
) -> None:
    """Re-ask ``question`` against the offered session in Specific Session scope
    (BU093 YES), reusing the BU090 candidate-pick call shape rather than
    duplicating it.
    """
    run_query(
        question=question,
        agent_id=agent_id,
        explicit_scope="current_session",
        active_session_id=None,
        selected_session_id=offer.session_id,
    )


def decline_scope_offer(
    offer: ScopeOffer,
    conversation_id: Optional[int],
    record_decline: Callable[[Optional[int], int], Any],
) -> None:
    """Record that the user declined the offer (BU093 NO) so the same session is
    not offered again in this conversation.
    """
    record_decline(conversation_id, offer.session_id)
