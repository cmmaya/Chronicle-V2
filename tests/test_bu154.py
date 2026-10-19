"""BU154 - Monochrome palette."""
import re

from src.app import theme

CORP = theme.REMAP[theme.BORING_CORPORATE]
HEX = re.compile(r"^#[0-9A-F]{6}$")


def test_corporate_covers_every_colour_synthwave_recolours():
    missing = sorted(set(theme.REMAP[theme.SYNTHWAVE]) - set(CORP))
    assert missing == []


def test_keys_are_upper_case_hex():
    assert all(HEX.match(key) for key in CORP)
    assert all(re.match(r"^#[0-9A-Fa-f]{6}$", value) for value in CORP.values())


def test_no_value_is_a_classic_key():
    """A remapped colour must never be remapped again (BU129 rule)."""
    classic_keys = set(theme.REMAP[theme.SYNTHWAVE]) | set(CORP)
    clashes = sorted(v for v in CORP.values() if v.upper() in classic_keys)
    assert clashes == []


def test_no_accent_colours_besides_red():
    """Monochrome: every mapped colour is a grey, except the live/error reds
    and the setup wizard's quiet success green."""
    allowed = {"#EF4444", "#5C2626", "#2A1414", "#FCA5A5", "#F87171", "#FCA5A6",
               "#C42B1C", "#86C99A"}
    for value in CORP.values():
        r, g, b = (int(value[i:i + 2], 16) for i in (1, 3, 5))
        if value.upper() in allowed:
            continue
        assert max(r, g, b) - min(r, g, b) <= 9, value


def test_cream_controls_turn_into_grey_surfaces_with_light_text():
    assert CORP["#F6E0A6"] == theme.ROLES[theme.BORING_CORPORATE]["surface"]
    assert CORP["#071846"].upper().startswith("#ECECE")


def test_corporate_roles_cover_the_classic_ones():
    assert set(theme.ROLES[theme.CLASSIC]) <= set(theme.ROLES[theme.BORING_CORPORATE])


def test_extra_qss_is_native_and_styles_popups():
    qss = theme._EXTRA_QSS[theme.BORING_CORPORATE]
    assert qss.startswith(theme.NATIVE_QSS)
    for selector in ("QMenu", "QToolTip", "QScrollBar:vertical", "QComboBox QAbstractItemView",
                     "QFrame#ChatInputBar", "QFrame#UnifiedSearchBar", "QMessageBox"):
        assert selector in qss
