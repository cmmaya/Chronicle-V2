"""BU098 - Active pane focus model.

Plain-Python, no Qt harness (same constraint as BU093/BU095/BU096, and PySide6
is not importable here). ``PaneFocusController`` / ``resolve_pane_for_widget``
live in ``src/app/window.py``, whose module-level imports pull in Qt and the
audio stack, so we extract just those two self-contained definitions from the
source and exec them in isolation - the real code is still what gets tested.
"""
import ast
import os
import typing

_WINDOW_PATH = os.path.join(
    os.path.dirname(__file__), "..", "src", "app", "window.py"
)
_WANTED = {"PaneFocusController", "resolve_pane_for_widget"}


def _load_pane_focus():
    with open(_WINDOW_PATH, "r", encoding="utf-8") as fh:
        source = fh.read()
    tree = ast.parse(source)
    ns = {"Optional": typing.Optional}
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in _WANTED:
            exec(compile(ast.Module([node], []), _WINDOW_PATH, "exec"), ns)
    return ns


_ns = _load_pane_focus()
PaneFocusController = _ns["PaneFocusController"]
resolve_pane_for_widget = _ns["resolve_pane_for_widget"]


def test_set_active_from_empty():
    c = PaneFocusController()
    assert c.set_active("left") == {"left"}
    assert c.active == "left"


def test_switch_returns_both_changed():
    c = PaneFocusController()
    c.set_active("left")
    assert c.set_active("right") == {"left", "right"}
    assert c.active == "right"


def test_reselect_current_is_noop():
    c = PaneFocusController()
    c.set_active("center")
    assert c.set_active("center") == set()
    assert c.active == "center"


def test_invalid_name_is_noop():
    c = PaneFocusController()
    c.set_active("center")
    assert c.set_active("bogus") == set()
    assert c.active == "center"


class _FakeWidget:
    def __init__(self, parent=None):
        self._parent = parent

    def parent(self):
        return self._parent


def test_resolve_pane_for_widget_walks_parent_chain():
    left, center, right = _FakeWidget(), _FakeWidget(), _FakeWidget()
    shells = {"left": left, "center": center, "right": right}
    child = _FakeWidget(parent=_FakeWidget(parent=center))
    assert resolve_pane_for_widget(child, shells) == "center"


def test_resolve_pane_for_unparented_widget_is_none():
    shells = {"left": _FakeWidget(), "center": _FakeWidget(), "right": _FakeWidget()}
    assert resolve_pane_for_widget(_FakeWidget(), shells) is None
