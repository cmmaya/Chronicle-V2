"""Tests for the single-session scope switch offer (BU092 / BU093)."""

from datetime import datetime

import pytest

from src.assistant.scope_offer import (
    ScopeOffer,
    accept_scope_offer,
    build_scope_offer,
    decline_scope_offer,
    format_scope_offer_prompt,
)


def _session(session_id, score, name=None, start_time=None):
    entry = {"session_id": session_id, "score": score}
    if name is not None:
        entry["session_name"] = name
    if start_time is not None:
        entry["start_time"] = start_time
    return entry


# --- Triggering ------------------------------------------------------------

def test_single_routed_session_yields_offer_with_full_payload():
    offer = build_scope_offer(
        [_session(7, 0.4, "Budget Review", 1700)],
        intent="detail",
        evidence="partial",
        declined_session_ids=set(),
    )
    assert offer == ScopeOffer(session_id=7, session_name="Budget Review", start_time=1700)


def test_top_routed_session_is_offered_when_nothing_is_cited():
    routed = [_session(3, 0.9, "Planning"), _session(4, 0.6, "Standup"), _session(5, 0.1, "Retro")]
    offer = build_scope_offer(routed, "detail", "partial", set())
    assert offer is not None and offer.session_id == 3 and offer.session_name == "Planning"


def test_partial_evidence_alone_triggers_the_offer():
    offer = build_scope_offer([_session(1, 0.5, "Only")], "overview", "partial", set())
    assert offer is not None


def test_near_tied_candidates_still_yield_an_offer_for_the_top():
    # Ambiguity is handled by the prompt's "choose another session" path, not here.
    routed = [_session(1, 0.50, "Top"), _session(2, 0.45, "Close second")]
    offer = build_scope_offer(routed, "detail", "partial", set())
    assert offer is not None and offer.session_id == 1


# --- Cited-session preference (BU093) ------------------------------------

def test_cited_session_is_offered_even_when_not_the_top_routed():
    routed = [_session(1, 0.9, "Top by score"), _session(7, 0.2, "The one cited")]
    offer = build_scope_offer(routed, "overview", "sufficient", set(), cited_session_ids=[7])
    assert offer is not None and offer.session_id == 7 and offer.session_name == "The one cited"


def test_overview_sufficient_answer_that_cites_a_session_still_offers():
    # "The discussion is found in Session 41" - an overview answer that names a session.
    routed = [_session(41, 0.8, "Potentials in physics")]
    offer = build_scope_offer(routed, "overview", "sufficient", set(), cited_session_ids=[41])
    assert offer is not None and offer.session_id == 41


def test_overview_sufficient_answer_without_any_cited_session_yields_none():
    routed = [_session(1, 0.99, "Very dominant")]
    assert build_scope_offer(routed, "overview", "sufficient", set(), cited_session_ids=[]) is None


def test_cited_session_not_among_routed_falls_back_to_top_routed():
    routed = [_session(1, 0.9, "Top"), _session(2, 0.3, "Other")]
    offer = build_scope_offer(routed, "overview", "sufficient", set(), cited_session_ids=[999])
    assert offer is not None and offer.session_id == 1


# --- Non-triggering --------------------------------------------------------

def test_evidence_none_yields_none_even_when_a_session_is_cited():
    # The answer is in no routed session - do not offer to switch into one.
    routed = [_session(41, 0.99, "Potentials in physics")]
    assert build_scope_offer(routed, "detail", "none", set(), cited_session_ids=[41]) is None


def test_no_routed_sessions_yields_none():
    assert build_scope_offer([], "detail", "partial", set()) is None
    assert build_scope_offer(None, "detail", "partial", set()) is None


def test_declined_target_session_is_skipped_and_not_replaced():
    routed = [_session(1, 0.90, "Declined one"), _session(2, 0.20, "Runner up")]
    assert build_scope_offer(routed, "detail", "partial", {1}) is None


def test_decline_of_another_session_does_not_suppress_the_offer():
    routed = [_session(1, 0.90, "Top")]
    offer = build_scope_offer(routed, "detail", "partial", {99})
    assert offer is not None and offer.session_id == 1


# --- Totality on malformed input ----------------------------------------

def test_missing_score_does_not_prevent_an_offer():
    routed = [{"session_id": 1, "session_name": "Top"}, {"session_id": 2, "session_name": "Other"}]
    offer = build_scope_offer(routed, "detail", "partial", set())
    assert offer is not None and offer.session_id == 1


def test_missing_session_name_falls_back_to_generic_label():
    offer = build_scope_offer([{"session_id": 42, "score": 0.7}], "detail", "partial", set())
    assert offer is not None
    assert offer.session_name == "Session 42"
    assert offer.start_time is None


def test_non_dict_entries_after_the_target_do_not_raise():
    routed = [{"session_id": 1, "session_name": "Top", "score": "not-a-number"}, None, "junk"]
    offer = build_scope_offer(routed, "detail", "partial", set())
    assert offer is not None and offer.session_id == 1


def test_missing_session_id_on_the_top_entry_yields_none():
    assert build_scope_offer([{"session_name": "Nameless", "score": 0.9}], "detail", "partial", set()) is None


def test_non_dict_top_entry_yields_none():
    assert build_scope_offer(["junk"], "detail", "partial", set()) is None


def test_declined_ids_may_be_omitted():
    offer = build_scope_offer([_session(1, 0.9, "Top")], "detail", "partial")
    assert offer is not None


# --- BU093: prompt text --------------------------------------------------

def test_prompt_renders_the_exact_required_sentence():
    ts = int(datetime(2024, 3, 7, 9, 30).timestamp())
    assert format_scope_offer_prompt("Budget Review", ts) == (
        "Would you like to change the scope to Specific for session: "
        "Budget Review, 2024-03-07 09:30?"
    )


@pytest.mark.parametrize("bad_start", [None, 0, "", "not-a-timestamp", float("nan"), 10**20])
def test_prompt_drops_date_and_comma_for_missing_or_unparseable_start(bad_start):
    result = format_scope_offer_prompt("Weekly Sync", bad_start)
    assert result == (
        "Would you like to change the scope to Specific for session: Weekly Sync?"
    )
    assert ", ?" not in result
    assert "None" not in result


def test_prompt_may_be_called_without_a_start_time():
    assert format_scope_offer_prompt("Standup") == (
        "Would you like to change the scope to Specific for session: Standup?"
    )


def test_session_name_with_punctuation_is_not_mangled_or_escaped():
    ts = int(datetime(2024, 1, 2, 8, 0).timestamp())
    result = format_scope_offer_prompt("Q3 Review: budget, scope & risks", ts)
    assert "Q3 Review: budget, scope & risks" in result
    assert "&amp;" not in result and "\\" not in result


# --- BU093: accept / decline plumbing ----------------------------------

def test_accepting_reasks_in_specific_session_against_the_offered_session():
    offer = ScopeOffer(session_id=42, session_name="Planning", start_time=1700)
    calls = []
    accept_scope_offer(offer, "what did Ana say?", "chronicle_assistant", lambda **kw: calls.append(kw))

    assert calls == [dict(
        question="what did Ana say?",
        agent_id="chronicle_assistant",
        explicit_scope="current_session",
        active_session_id=None,
        selected_session_id=42,
    )]


def test_declining_records_the_decline_with_conversation_and_session_id():
    offer = ScopeOffer(session_id=9, session_name="Retro")
    calls = []
    decline_scope_offer(offer, 55, lambda conv, sid: calls.append((conv, sid)))
    assert calls == [(55, 9)]


def test_declining_in_a_brand_new_conversation_passes_none_conversation_id():
    offer = ScopeOffer(session_id=9, session_name="Retro")
    calls = []
    decline_scope_offer(offer, None, lambda conv, sid: calls.append((conv, sid)))
    assert calls == [(None, 9)]
