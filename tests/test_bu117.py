"""BU117 (superseded by BU151): the loader and excerpt pieces session documents reuse.

The window-held attachment this BU introduced - its chip, drop rules and
double send - is gone; ``tests/test_bu151.py`` covers the session-documents
replacement. What remains here is the text decoding and per-question excerpt
choice that ``session_documents`` still builds on.
"""
import unittest
from types import SimpleNamespace

from src.assistant.reference_doc import decode_text, select_relevant_excerpt

SYLLABUS = """Course outline

Exercise 4 asks for the divergence theorem applied to a cube.

Exercise 5 covers Stokes' theorem on an open surface.

Office hours are Thursdays at four in the afternoon.
"""


class DecodeTextTest(unittest.TestCase):
    def test_utf8_decodes(self):
        self.assertEqual(decode_text("café ✓".encode("utf-8")), "café ✓")

    def test_a_platform_encoded_file_falls_back(self):
        self.assertEqual(decode_text("café".encode("cp1252")), "café")


# =========================
# Excerpt selection
# =========================

def _doc(text):
    return SimpleNamespace(text=text)


class SelectExcerptTest(unittest.TestCase):
    def setUp(self):
        self.doc = _doc(SYLLABUS)

    def test_the_matching_paragraph_is_chosen(self):
        excerpt = select_relevant_excerpt(
            self.doc, "what does exercise 5 cover?", max_chars=80)
        self.assertIn("Exercise 5", excerpt)
        self.assertNotIn("Office hours", excerpt)

    def test_a_different_question_chooses_a_different_paragraph(self):
        excerpt = select_relevant_excerpt(
            self.doc, "when are office hours?", max_chars=80)
        self.assertIn("Office hours", excerpt)

    def test_the_excerpt_stays_under_the_cap(self):
        excerpt = select_relevant_excerpt(
            self.doc, "exercise 4 divergence", max_chars=60)
        self.assertLessEqual(len(excerpt), 60)

    def test_a_generous_cap_keeps_neighbouring_paragraphs(self):
        excerpt = select_relevant_excerpt(
            self.doc, "exercise 4 divergence", max_chars=4000)
        self.assertIn("Exercise 4", excerpt)
        self.assertIn("Exercise 5", excerpt)

    def test_the_same_question_always_yields_the_same_excerpt(self):
        first = select_relevant_excerpt(self.doc, "exercise", max_chars=90)
        for _ in range(5):
            self.assertEqual(
                select_relevant_excerpt(self.doc, "exercise", max_chars=90),
                first,
            )

    def test_equal_scoring_spans_resolve_to_the_earliest(self):
        # Both paragraphs score identically on "topic"; the first one wins.
        doc = _doc("topic alpha\n\ntopic beta")
        self.assertEqual(select_relevant_excerpt(doc, "topic", max_chars=11),
                         "topic alpha")

    def test_an_empty_document_yields_nothing(self):
        self.assertEqual(select_relevant_excerpt(_doc(""), "anything"), "")

    def test_no_document_yields_nothing(self):
        self.assertEqual(select_relevant_excerpt(None, "anything"), "")

    def test_a_question_matching_nothing_falls_back_to_the_top(self):
        excerpt = select_relevant_excerpt(
            self.doc, "zzz qqq", max_chars=4000)
        self.assertTrue(excerpt.startswith("Course outline"))

    def test_a_paragraph_longer_than_the_cap_is_truncated(self):
        doc = _doc("x" * 500)
        excerpt = select_relevant_excerpt(doc, "x", max_chars=50)
        self.assertEqual(len(excerpt), 50)

    def test_a_zero_cap_yields_nothing(self):
        self.assertEqual(select_relevant_excerpt(self.doc, "exercise", 0), "")
