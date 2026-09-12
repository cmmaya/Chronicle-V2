"""BU100 - Collapsible sectioned summary view: the pure parsing/formatting core.

Plain-Python, no Qt harness (PySide6 is not importable here). The parser lives
in src/app/pixel_widgets.py alongside Qt widgets, so just the self-contained
regexes and functions are extracted from source and exec'd in isolation - the
real code is still what gets tested. The widget and dialog wiring is verified
manually per the BU.
"""
import ast
import html as html_escape
import os
import re

_PIXEL_WIDGETS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "src", "app", "pixel_widgets.py"
)

_CONSTANTS = {
    "_SUMMARY_SECTION_ALIASES",
    "_HEADER_DECORATION_RE",
    "_NUMBERED_LINE_RE",
    "_BULLET_LINE_RE",
    "_FIELD_LINE_RE",
    "_MD_BOLD_RE",
}
_FUNCTIONS = {
    "escape_with_markdown_bold",
    "_section_title_for",
    "parse_summary_sections",
    "count_summary_items",
    "format_summary_body_html",
}


def _load_summary_helpers():
    with open(_PIXEL_WIDGETS_PATH, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    ns = {"re": re, "html_escape": html_escape}
    for node in tree.body:
        wanted = (
            isinstance(node, ast.FunctionDef) and node.name in _FUNCTIONS
        ) or (
            isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id in _CONSTANTS for t in node.targets
            )
        )
        if wanted:
            exec(compile(ast.Module([node], []), _PIXEL_WIDGETS_PATH, "exec"), ns)
    return ns


_NS = _load_summary_helpers()
parse_summary_sections = _NS["parse_summary_sections"]
count_summary_items = _NS["count_summary_items"]
format_summary_body_html = _NS["format_summary_body_html"]


# A summary shaped like what templates.py (FULL / GENERAL_TRANSCRIPT) produces,
# including model line wrapping mid-sentence.
TEMPLATE_SUMMARY = """1. Overview
The team reviewed the migration status and agreed on the remaining
blockers before rollout.

2. Key Points
1. The migration is 80% complete across the ingest services.
2. Latency regressions were traced back to the backfill job running
during peak hours.

3. Action Items
Title: Finish schema migration
Description: Apply the pending updates.
Due date: 2026-09-20
Responsible: Camilo

Title: Re-run backfill
Description: Re-run once the schema lands.
Due date: Not specified
Responsible: Not specified

4. Decisions
1. Postpone the announcement.

5. Open Questions or Unclear Points
- Whether the watchdog covers the fallback path.
"""


def test_parses_every_template_section_in_order():
    titles = [t for t, _ in parse_summary_sections(TEMPLATE_SUMMARY)]
    assert titles == [
        "Overview",
        "Key Points",
        "Action Items",
        "Decisions",
        "Open Questions",
    ]


def test_section_bodies_keep_their_own_content():
    sections = dict(parse_summary_sections(TEMPLATE_SUMMARY))
    assert sections["Overview"].startswith("The team reviewed")
    assert "Postpone the announcement" in sections["Decisions"]
    assert "Postpone" not in sections["Action Items"]
    assert "Title: Finish schema migration" in sections["Action Items"]


def test_markdown_decorated_headers_are_recognized():
    text = "## 1. Overview\nProse.\n\n**Key Points**\n1. A point.\n\n### Action Items:\nNone."
    assert [t for t, _ in parse_summary_sections(text)] == [
        "Overview",
        "Key Points",
        "Action Items",
    ]


def test_numbered_body_lines_are_not_mistaken_for_headers():
    # The whole point of matching known section names only: a key point that
    # happens to read like a header must stay inside its section.
    text = "2. Key Points\n1. Decisions were deferred.\n2. Overview of costs was shared."
    sections = parse_summary_sections(text)
    assert len(sections) == 1
    assert sections[0][0] == "Key Points"
    assert "Overview of costs" in sections[0][1]


def test_unknown_content_falls_back_to_one_section_losing_nothing():
    text = "A blob of legacy text.\nWith a second line."
    assert parse_summary_sections(text) == [("Summary", text)]


def test_model_lead_in_before_the_first_header_is_dropped():
    # "Here's a comprehensive summary of the meeting:" is chatter, not content.
    text = "Here's a comprehensive summary of the meeting:\n\n2. Key Points\n1. A point."
    sections = parse_summary_sections(text)
    assert [t for t, _ in sections] == ["Key Points"]
    assert "comprehensive summary" not in sections[0][1]


def test_empty_summary_yields_no_sections():
    assert parse_summary_sections("") == []
    assert parse_summary_sections("   \n  ") == []
    assert parse_summary_sections(None) == []


def test_count_items_by_shape():
    sections = dict(parse_summary_sections(TEMPLATE_SUMMARY))
    assert count_summary_items(sections["Key Points"]) == 2
    assert count_summary_items(sections["Action Items"]) == 2  # Title: blocks
    assert count_summary_items(sections["Open Questions"]) == 1  # bullet
    assert count_summary_items(sections["Overview"]) == 0  # prose


def test_format_escapes_html_in_content():
    out = format_summary_body_html("Tom & Jerry <script>alert(1)</script>")
    assert "Tom &amp; Jerry" in out
    assert "<script>" not in out


def test_format_reflows_hard_wrapped_prose_into_one_block():
    out = format_summary_body_html("The team agreed on the remaining\nblockers before rollout.")
    assert out.count("<div") == 1
    assert "remaining blockers" in out


def test_format_reflows_wrapped_numbered_entries():
    sections = dict(parse_summary_sections(TEMPLATE_SUMMARY))
    out = format_summary_body_html(sections["Key Points"])
    assert out.count("<div") == 2
    assert "backfill job running during peak hours" in out


def test_format_separates_action_item_cards():
    sections = dict(parse_summary_sections(TEMPLATE_SUMMARY))
    out = format_summary_body_html(sections["Action Items"])
    assert out.count("<hr") == 1  # a rule between the two cards, none before the first
    assert "Finish schema migration" in out
    assert "Due date:" in out


def test_format_blank_body_reads_as_not_specified():
    assert "Not specified" in format_summary_body_html("")


def test_format_renders_markdown_bold():
    out = format_summary_body_html("The **budget** was approved.")
    assert "<b>budget</b>" in out
    assert "**" not in out


def test_format_bold_survives_escaping_and_reflow():
    # Escaping runs first, so a bold span carrying HTML-special characters is
    # still escaped; and a span split across the model's hard wrap still closes.
    out = format_summary_body_html("Owner is **A & B**.\nThe **second\nline** counts.")
    assert "<b>A &amp; B</b>" in out
    assert "<b>second line</b>" in out


def test_format_leaves_unpaired_asterisks_alone():
    out = format_summary_body_html("A 2**3 calculation.")
    assert "<b>" not in out
