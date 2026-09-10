"""Tests for the Any Session answer contract parser (BU090)."""

from src.assistant.response_contract import (
    META_SENTINEL,
    RESPONSE_CONTRACT,
    parse_answer,
    should_hand_off,
)


def test_well_formed_response_is_split_cleanly():
    raw = (
        "The budget meeting was on Tuesday.\n\n"
        f'{META_SENTINEL}\n'
        '{"intent":"overview","evidence":"sufficient","sessions":[12,7]}'
    )
    parsed = parse_answer(raw)

    assert parsed.answer_text == "The budget meeting was on Tuesday."
    assert parsed.intent == "overview"
    assert parsed.evidence == "sufficient"
    assert parsed.sessions == [12, 7]


def test_missing_sentinel_returns_full_text_with_null_metadata():
    raw = "Just a normal answer with no trailer."
    parsed = parse_answer(raw)

    assert parsed.answer_text == raw
    assert parsed.intent is None
    assert parsed.evidence is None
    assert parsed.sessions == []


def test_malformed_json_degrades_without_raising():
    raw = f"Answer body.\n\n{META_SENTINEL}\n{{not valid json at all"
    parsed = parse_answer(raw)

    assert parsed.answer_text == raw
    assert parsed.intent is None
    assert parsed.evidence is None
    assert parsed.sessions == []


def test_sentinel_appearing_twice_splits_on_last_occurrence():
    raw = (
        f"I mentioned {META_SENTINEL} earlier by mistake.\n\n"
        f'{META_SENTINEL}\n'
        '{"intent":"detail","evidence":"partial","sessions":[3]}'
    )
    parsed = parse_answer(raw)

    assert parsed.answer_text == f"I mentioned {META_SENTINEL} earlier by mistake."
    assert parsed.intent == "detail"
    assert parsed.evidence == "partial"
    assert parsed.sessions == [3]


def test_unknown_enum_values_are_coerced_to_null():
    raw = (
        f"Body.\n\n{META_SENTINEL}\n"
        '{"intent":"speculation","evidence":"maybe","sessions":[1]}'
    )
    parsed = parse_answer(raw)

    assert parsed.answer_text == "Body."
    assert parsed.intent is None
    assert parsed.evidence is None
    assert parsed.sessions == [1]


def test_none_input_does_not_raise():
    parsed = parse_answer(None)
    assert parsed.answer_text == ""
    assert parsed.intent is None


def test_handoff_condition_triggers_on_detail_intent():
    assert should_hand_off("detail", "sufficient") is True
    assert should_hand_off("detail", "partial") is True


def test_handoff_condition_triggers_on_partial_evidence():
    assert should_hand_off("overview", "partial") is True


def test_handoff_condition_false_when_evidence_is_none():
    # The answer is in no routed session - nothing to hand off to.
    assert should_hand_off("detail", "none") is False
    assert should_hand_off("overview", "none") is False


def test_handoff_condition_false_for_overview_with_sufficient_evidence():
    assert should_hand_off("overview", "sufficient") is False
    assert should_hand_off(None, None) is False


def test_contract_text_contains_sentinel():
    assert META_SENTINEL in RESPONSE_CONTRACT
