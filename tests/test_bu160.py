"""BU160 - Visual QA: contrast of the Boring Corporate palette.

The screenshots of every window were compared by hand (see BU160.md); what a
test can hold is the contrast of each text colour on the surfaces it sits on.
"""
import pytest

from src.app import theme

ROLES = theme.ROLES[theme.BORING_CORPORATE]


def _luminance(value: str) -> float:
    def channel(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(value[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(fg: str, bg: str) -> float:
    a, b = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


@pytest.mark.parametrize("text", ["text", "text_secondary", "text_muted"])
@pytest.mark.parametrize("surface", ["canvas", "surface", "nav_selected"])
def test_text_meets_wcag_aa(text, surface):
    assert contrast(ROLES[text], ROLES[surface]) >= 4.5


def test_primary_button_and_find_chip_contrast():
    assert contrast(ROLES["primary_text"], ROLES["primary_fill"]) >= 7
    assert contrast(ROLES["find_text"], ROLES["find_fill"]) >= 7


def test_muted_on_black_matches_the_plan():
    assert contrast("#8F8F8F", "#000000") == pytest.approx(6.6, abs=0.2)
