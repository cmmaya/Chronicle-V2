"""BU117 - a dropped .txt used as answer-side evidence."""
import os
import shutil
import tempfile
import unittest

from src.assistant.live_qa import answer_instruction
from src.assistant.reference_doc import (
    REFERENCE_BLOCK_PREAMBLE, ReferenceDoc, ReferenceDocError,
    build_reference_block, is_allowed_extension, load_reference_text,
    select_relevant_excerpt,
)
from PySide6.QtWidgets import QWidget

from src.app.window import reference_drop_error
from src.config import REFERENCE_DOC
from tests.detached_harness import DetachedHarness, app

_app = app()

SYLLABUS = """Course outline

Exercise 4 asks for the divergence theorem applied to a cube.

Exercise 5 covers Stokes' theorem on an open surface.

Office hours are Thursdays at four in the afternoon.
"""


class _TempDir(unittest.TestCase):
    """Base class giving each test its own directory of fixture files."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def write(self, name, content, encoding="utf-8"):
        path = os.path.join(self.dir, name)
        mode = "wb" if isinstance(content, bytes) else "w"
        kwargs = {} if isinstance(content, bytes) else {"encoding": encoding}
        with open(path, mode, **kwargs) as handle:
            handle.write(content)
        return path


# =========================
# Loading
# =========================

class LoadReferenceTextTest(_TempDir):
    def test_a_utf8_file_loads(self):
        doc = load_reference_text(self.write("syllabus.txt", SYLLABUS))
        self.assertEqual(doc.name, "syllabus.txt")
        self.assertIn("divergence theorem", doc.text)
        self.assertEqual(doc.char_count, len(doc.text))

    def test_utf8_characters_survive(self):
        doc = load_reference_text(self.write("notes.txt", "resumen: integración\n"))
        self.assertIn("integración", doc.text)

    def test_a_platform_encoded_file_falls_back(self):
        # A file a Windows editor saved as cp1252 is not valid UTF-8.
        path = self.write("cp1252.txt", "caf\xe9 at four\n".encode("cp1252"))
        doc = load_reference_text(path)
        self.assertIn("at four", doc.text)

    def test_line_endings_are_normalised(self):
        doc = load_reference_text(self.write("crlf.txt", b"one\r\n\r\ntwo\r\n"))
        self.assertNotIn("\r", doc.text)
        self.assertIn("one\n\ntwo", doc.text)

    def test_an_oversize_file_is_refused(self):
        path = self.write("big.txt", "x" * 5000)
        with self.assertRaises(ReferenceDocError) as caught:
            load_reference_text(path, max_bytes=1000)
        self.assertIn("limit", str(caught.exception))

    def test_a_missing_file_is_refused(self):
        with self.assertRaises(ReferenceDocError):
            load_reference_text(os.path.join(self.dir, "nope.txt"))

    def test_a_folder_is_refused(self):
        with self.assertRaises(ReferenceDocError):
            load_reference_text(self.dir)

    def test_a_disallowed_extension_is_refused(self):
        with self.assertRaises(ReferenceDocError):
            load_reference_text(self.write("handout.pdf", "not really a pdf"))

    def test_binary_content_behind_a_txt_name_is_refused(self):
        path = self.write("fake.txt", b"\x89PNG\x00\x1a\n\x00\x00")
        with self.assertRaises(ReferenceDocError) as caught:
            load_reference_text(path)
        self.assertIn("not readable text", str(caught.exception))

    def test_an_empty_file_loads_as_an_empty_document(self):
        doc = load_reference_text(self.write("empty.txt", ""))
        self.assertEqual(doc.text, "")
        self.assertEqual(doc.char_count, 0)

    def test_the_size_label_reads_in_kb_for_a_big_document(self):
        doc = load_reference_text(self.write("big.txt", "x" * 4096))
        self.assertEqual(doc.size_label, "4 KB")

    def test_the_size_label_reads_in_bytes_for_a_small_one(self):
        doc = load_reference_text(self.write("small.txt", "x" * 40))
        self.assertEqual(doc.size_label, "40 B")


class ExtensionValidationTest(unittest.TestCase):
    def test_txt_is_allowed(self):
        self.assertTrue(is_allowed_extension("C:/x/syllabus.txt"))

    def test_the_check_ignores_case(self):
        self.assertTrue(is_allowed_extension("C:/x/SYLLABUS.TXT"))

    def test_other_formats_are_not_allowed(self):
        for name in ("a.pdf", "a.docx", "a.md", "a", "a.txt.pdf"):
            with self.subTest(name=name):
                self.assertFalse(is_allowed_extension(name))

    def test_only_txt_is_configured(self):
        self.assertEqual(REFERENCE_DOC["allowed_extensions"], [".txt"])


class DropValidationTest(_TempDir):
    def test_a_single_txt_is_accepted(self):
        self.assertIsNone(reference_drop_error([self.write("a.txt", "x")]))

    def test_a_pdf_is_refused(self):
        error = reference_drop_error([self.write("a.pdf", "x")])
        self.assertIn(".txt", error)

    def test_a_folder_is_refused(self):
        error = reference_drop_error([self.dir])
        self.assertIn("Folders", error)

    def test_two_files_are_refused(self):
        error = reference_drop_error(
            [self.write("a.txt", "x"), self.write("b.txt", "y")]
        )
        self.assertIn("one .txt", error)

    def test_a_drop_with_no_local_file_is_refused(self):
        self.assertTrue(reference_drop_error([]))


# =========================
# Excerpt selection
# =========================

def _doc(text, name="syllabus.txt"):
    return ReferenceDoc(name=name, path=f"C:/x/{name}", text=text,
                        char_count=len(text), loaded_at=None)


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


class ReferenceBlockTest(unittest.TestCase):
    def test_the_block_is_labelled(self):
        block = build_reference_block("Exercise 4 asks for the divergence theorem.")
        self.assertTrue(block.startswith(REFERENCE_BLOCK_PREAMBLE))
        self.assertIn("Exercise 4", block)

    def test_an_empty_excerpt_has_no_block(self):
        self.assertEqual(build_reference_block("   "), "")
        self.assertEqual(build_reference_block(None), "")

    def test_the_block_is_distinct_from_a_transcript_line(self):
        self.assertNotIn("[", REFERENCE_BLOCK_PREAMBLE)


# =========================
# The instruction tier
# =========================

class ReferenceInstructionTest(unittest.TestCase):
    def test_transcripts_mode_gains_the_document_tier(self):
        text = answer_instruction("transcripts", "syllabus.txt")
        self.assertIn("From transcripts:", text)
        self.assertIn("From syllabus.txt:", text)
        self.assertIn("General knowledge:", text)

    def test_the_tiers_are_in_evidence_order(self):
        text = answer_instruction("transcripts", "syllabus.txt")
        self.assertLess(text.index("From transcripts:"),
                        text.index("From syllabus.txt:"))
        self.assertLess(text.index("From syllabus.txt:"),
                        text.index("General knowledge:"))

    def test_without_a_document_the_two_tier_prompt_is_used(self):
        text = answer_instruction("transcripts")
        self.assertNotIn("reference document", text)

    def test_general_mode_ignores_the_document(self):
        self.assertEqual(answer_instruction("general", "syllabus.txt"),
                         answer_instruction("general"))


# =========================
# The window
# =========================

class _Harness(DetachedHarness):
    """Captures what would have been asked instead of asking it."""

    def __init__(self, prefs_path=None):
        if prefs_path is None:
            fd, prefs_path = tempfile.mkstemp(suffix=".json")
            os.close(fd)
        super().__init__(prefs_path=prefs_path)
        self.assistant_service = object()
        self.agent_combo = _AgentCombo()
        # _on_detach_transcription hides the main window's transcripts panel.
        self.right_shell = QWidget()
        self.asked = []

    def _start_detached_answer(self, card_id, question):
        # The real method is what appends the reference block, so the tests
        # that care about that call it through _with_reference_evidence.
        self.asked.append((card_id, question))


class _AgentCombo:
    @staticmethod
    def currentData():
        return "research_helper"


class AttachTest(_TempDir):
    def setUp(self):
        super().setUp()
        self.h = _Harness()
        self.h.build_answers_column()
        self.path = self.write("syllabus.txt", SYLLABUS)

    def test_a_dropped_txt_attaches_and_names_its_chip(self):
        self.h._attach_reference_doc(self.path)
        self.assertEqual(self.h._reference_doc.name, "syllabus.txt")
        self.assertFalse(self.h._reference_chip.isHidden())
        self.assertIn("syllabus.txt", self.h._reference_chip.name_button.text())

    def test_the_chip_shows_the_size(self):
        self.h._attach_reference_doc(self.path)
        self.assertIn(self.h._reference_doc.size_label,
                      self.h._reference_chip.name_button.text())

    def test_there_is_no_chip_until_something_is_attached(self):
        self.assertTrue(self.h._reference_chip.isHidden())

    def test_attaching_reports_it(self):
        self.h._attach_reference_doc(self.path)
        self.assertIn("Attached syllabus.txt", self.h.status_messages[-1][0])

    def test_a_second_drop_replaces_the_first_and_says_so(self):
        self.h._attach_reference_doc(self.path)
        second = self.write("agenda.txt", "Agenda\n\nItem one.")
        self.h._attach_reference_doc(second)
        self.assertEqual(self.h._reference_doc.name, "agenda.txt")
        self.assertIn("Replaced syllabus.txt", self.h.status_messages[-1][0])

    def test_only_one_document_is_ever_held(self):
        self.h._attach_reference_doc(self.path)
        self.h._attach_reference_doc(self.write("agenda.txt", "Agenda"))
        self.assertEqual(self.h._reference_doc.name, "agenda.txt")

    def test_removing_it_clears_the_chip(self):
        self.h._attach_reference_doc(self.path)
        self.h._clear_reference_doc()
        self.assertIsNone(self.h._reference_doc)
        self.assertTrue(self.h._reference_chip.isHidden())

    def test_an_oversize_file_is_refused_visibly(self):
        original = REFERENCE_DOC["max_bytes"]
        REFERENCE_DOC["max_bytes"] = 10
        try:
            self.h._attach_reference_doc(self.path)
        finally:
            REFERENCE_DOC["max_bytes"] = original
        self.assertIsNone(self.h._reference_doc)
        self.assertTrue(self.h.status_messages[-1][1])  # is_error

    def test_a_failed_attach_leaves_the_previous_document_intact(self):
        self.h._attach_reference_doc(self.path)
        self.h._attach_reference_doc(os.path.join(self.dir, "missing.txt"))
        self.assertEqual(self.h._reference_doc.name, "syllabus.txt")
        self.assertTrue(self.h.status_messages[-1][1])

    def test_a_binary_file_leaves_the_previous_document_intact(self):
        self.h._attach_reference_doc(self.path)
        self.h._attach_reference_doc(self.write("fake.txt", b"\x00\x01\x02"))
        self.assertEqual(self.h._reference_doc.name, "syllabus.txt")


class EvidenceWiringTest(_TempDir):
    def setUp(self):
        super().setUp()
        self.h = _Harness()
        self.h.build_answers_column()
        self.h._attach_reference_doc(self.write("syllabus.txt", SYLLABUS))

    def test_the_excerpt_is_appended_in_transcripts_mode(self):
        question = self.h._with_reference_evidence("what is exercise 4?")
        self.assertIn(REFERENCE_BLOCK_PREAMBLE, question)
        self.assertIn("divergence theorem", question)

    def test_the_original_question_survives_intact(self):
        question = self.h._with_reference_evidence("what is exercise 4?")
        self.assertTrue(question.startswith("what is exercise 4?"))

    def test_general_knowledge_mode_appends_nothing(self):
        self.h._set_live_qa_answer_mode("general")
        question = self.h._with_reference_evidence("what is exercise 4?")
        self.assertEqual(question, "what is exercise 4?")

    def test_general_knowledge_mode_names_no_document(self):
        self.h._set_live_qa_answer_mode("general")
        self.assertEqual(self.h._reference_evidence_name(), "")

    def test_transcripts_mode_names_the_document(self):
        self.assertEqual(self.h._reference_evidence_name(), "syllabus.txt")

    def test_nothing_is_appended_once_the_document_is_removed(self):
        self.h._clear_reference_doc()
        self.assertEqual(self.h._with_reference_evidence("q"), "q")
        self.assertEqual(self.h._reference_evidence_name(), "")

    def test_an_empty_document_appends_nothing(self):
        self.h._attach_reference_doc(self.write("empty.txt", ""))
        self.assertEqual(self.h._with_reference_evidence("q"), "q")


class LifecycleTest(_TempDir):
    """The document belongs to the open window and to nothing else."""

    def setUp(self):
        super().setUp()
        fd, self.prefs = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(self.prefs)
                        and os.unlink(self.prefs))
        self.h = _Harness(prefs_path=self.prefs)

    def test_closing_the_window_releases_the_document(self):
        self.h._on_detach_transcription()
        self.h._attach_reference_doc(self.write("syllabus.txt", SYLLABUS))
        self.assertIsNotNone(self.h._reference_doc)
        self.h._on_close_detached_window()
        self.assertIsNone(self.h._reference_doc)

    def test_nothing_about_it_reaches_preferences(self):
        self.h._on_detach_transcription()
        self.h._attach_reference_doc(self.write("syllabus.txt", SYLLABUS))
        self.h._save_live_qa_preferences()
        with open(self.prefs) as f:
            stored = f.read()
        self.assertNotIn("syllabus", stored)
        self.assertNotIn("reference", stored)

    def test_the_window_accepts_drops(self):
        self.h._on_detach_transcription()
        self.assertTrue(self.h._detached_window.acceptDrops())


if __name__ == "__main__":
    unittest.main()
