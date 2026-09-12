"""BU099 - In-pane find bar: the pure FindController match engine.

Plain-Python, no Qt harness (PySide6 is not importable here). FindController
lives in src/app/window.py, whose imports pull in Qt, so we extract just that
one self-contained class from source and exec it in isolation - the real code
is still what gets tested. The MainWindow DB adapter's swallow-on-error
behaviour is checked with a tiny stand-in that mirrors it.
"""
import ast
import html as html_escape
import os
import re

_WINDOW_PATH = os.path.join(
    os.path.dirname(__file__), "..", "src", "app", "window.py"
)
_PIXEL_WIDGETS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "src", "app", "pixel_widgets.py"
)


def _load_find_controller():
    with open(_WINDOW_PATH, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    ns = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "FindController":
            exec(compile(ast.Module([node], []), _WINDOW_PATH, "exec"), ns)
    return ns["FindController"]


def _load_highlight_terms_html():
    with open(_PIXEL_WIDGETS_PATH, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    ns = {"html_escape": html_escape, "re": re}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "highlight_terms_html":
            exec(compile(ast.Module([node], []), _PIXEL_WIDGETS_PATH, "exec"), ns)
    return ns["highlight_terms_html"]


FindController = _load_find_controller()
highlight_terms_html = _load_highlight_terms_html()

CENTER = ["alpha beta", "gamma", "Alpha GAMMA", "delta"]
RIGHT = ["Mic: hello world", "System: goodbye", "Mic: hello again"]


def _ctrl(search_conversations=lambda terms: []):
    return FindController(
        search_conversations,
        lambda: list(CENTER),
        lambda: list(RIGHT),
    )


def test_parse_terms():
    assert FindController.parse_terms("  Foo  BAR ") == ["foo", "bar"]
    assert FindController.parse_terms("") == []
    assert FindController.parse_terms("   ") == []


def test_center_single_and_multi_term():
    c = _ctrl()
    c.set_mode("center")
    c.set_query("alpha")
    assert c.matches == [0, 2]
    assert c.match_label() == (1, 2)

    c.set_query("alpha gamma")
    assert c.matches == [2]

    c.set_query("gamma alpha")  # order independent
    assert c.matches == [2]


def test_empty_query_no_matches():
    c = _ctrl()
    c.set_mode("center")
    c.set_query("")
    assert c.matches == []
    assert c.match_label() == (0, 0)
    assert c.current() is None


def test_next_prev_wrap_and_label():
    c = _ctrl()
    c.set_mode("right")
    c.set_query("mic")
    assert c.matches == [0, 2]
    assert c.match_label() == (1, 2)
    assert c.next() == 2
    assert c.match_label() == (2, 2)
    assert c.next() == 0  # wrap
    assert c.match_label() == (1, 2)
    assert c.prev() == 2  # wrap back
    assert c.match_label() == (2, 2)


def test_label_sequence_three_matches():
    c = _ctrl()
    c.set_mode("center")
    c.set_query("a")  # matches alpha/gamma/delta rows -> 0,1,2,3 actually
    # narrow to exactly three
    c._list_center_texts = lambda: ["ax", "ay", "az", "bb"]
    c.set_query("a")
    assert c.match_label() == (1, 3)
    c.next()
    assert c.match_label() == (2, 3)
    c.next()
    assert c.match_label() == (3, 3)
    c.next()
    assert c.match_label() == (1, 3)


def test_mode_switch_recomputes_and_resets_cursor():
    c = _ctrl()
    c.set_mode("center")
    c.set_query("gamma")
    assert c.matches == [1, 2]
    c.next()
    assert c.cursor == 1
    c.set_mode("right")
    assert c.matches == []  # "gamma" not in RIGHT
    assert c.cursor == -1


def test_left_mode_returns_conversation_ids_in_order():
    calls = {}

    def fake_search(terms):
        calls["terms"] = terms
        return [7, 3, 9]

    c = _ctrl(fake_search)
    c.set_mode("left")
    c.set_query("budget review")
    assert calls["terms"] == ["budget", "review"]
    assert c.matches == [7, 3, 9]
    assert c.current() == 7
    assert c.next() == 3


def test_left_mode_empty_result():
    c = _ctrl(lambda terms: [])
    c.set_mode("left")
    c.set_query("nothing")
    assert c.match_label() == (0, 0)
    assert c.current() is None


def test_invalid_mode_ignored():
    c = _ctrl()
    c.set_mode("center")
    c.set_mode("bogus")
    assert c.mode == "center"


def test_adapter_swallows_search_errors():
    """Mirror of MainWindow._find_search_conversations: a raising DB call
    yields [] so the controller reports (0, 0)."""

    class _DB:
        def search_conversations(self, *a, **k):
            raise RuntimeError("db down")

    def adapter(terms):
        try:
            return [r["id"] for r in _DB().search_conversations(" ".join(terms), limit=200)]
        except Exception:
            return []

    c = _ctrl(adapter)
    c.set_mode("left")
    c.set_query("anything")
    assert c.matches == []
    assert c.match_label() == (0, 0)


# --- in-text word highlight (BU099 follow-up) --------------------------

_BG, _FG = "#294F9D", "#FFF0BF"  # one arbitrary contrast pair for these tests


def test_highlight_terms_html_wraps_case_insensitive_matches():
    out = highlight_terms_html("The Quick brown fox", ["quick"], _BG, _FG)
    assert f'<span style="background:{_BG}; color:{_FG};">Quick</span>' in out
    assert out.startswith("The ")


def test_highlight_terms_html_multiple_terms():
    out = highlight_terms_html("alpha beta gamma", ["beta", "gamma"], _BG, _FG)
    assert out.count("<span") == 2
    assert "beta" in out and "gamma" in out


def test_highlight_terms_html_escapes_special_chars():
    out = highlight_terms_html(
        "<script>alert('x')</script> danger", ["danger"], _BG, _FG
    )
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "<span" in out


def test_highlight_terms_html_no_terms_returns_escaped_plain_text():
    out = highlight_terms_html("Tom & Jerry", [], _BG, _FG)
    assert out == "Tom &amp; Jerry"
    assert "<span" not in out


def test_highlight_terms_html_longest_term_wins_shadowing():
    # "cat" alone would otherwise chop "category" into a partial highlight.
    out = highlight_terms_html("category", ["cat", "category"], _BG, _FG)
    assert out == f'<span style="background:{_BG}; color:{_FG};">category</span>'


def test_highlight_terms_html_uses_the_given_contrast_pair():
    # Guards the actual bug reported: transcripts (cream System bubbles) must
    # use a highlight pair that differs from the bubble's own cream fill, not
    # the hardcoded chip color the chat-only bubbles happened to work with.
    on_cream = highlight_terms_html("hello world", ["hello"], "#294F9D", "#FFF0BF")
    on_blue = highlight_terms_html("hello world", ["hello"], "#FFEFC1", "#071846")
    assert "#294F9D" in on_cream and "#FFEFC1" not in on_cream
    assert "#FFEFC1" in on_blue and "#294F9D" not in on_blue
