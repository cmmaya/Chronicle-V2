"""BU119 - persisted UI state and a safe preferences file."""
import json
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QComboBox, QLabel, QLineEdit

from src.config import DEFAULT_MODEL, get_selected_model, set_selected_model
from src.app.window import _MUTE_TOGGLES
from tests.detached_harness import DetachedHarness, app

_app = app()


class _FakeDb:
    def __init__(self, sessions):
        self.sessions = sessions

    def get_session(self, session_id):
        return next((s for s in self.sessions if s['id'] == session_id), None)

    def list_sessions(self):
        return list(self.sessions)


class _FakeManager:
    def __init__(self, sessions=()):
        self.db = _FakeDb(list(sessions))
        self.muted = {'mic': False, 'system': False}

    def get_active_session(self):
        return None

    def is_source_muted(self, source):
        return self.muted[source]

    def set_source_muted(self, source, muted):
        self.muted[source] = bool(muted)
        return True


class PrefsHarness(DetachedHarness):
    """A MainWindow whose preferences file is a temporary one."""

    def __init__(self, tmpdir, sessions=()):
        super().__init__(prefs_path=os.path.join(tmpdir, 'preferences.json'))
        self.session_manager = _FakeManager(sessions)
        self.loaded_sessions = []
        self.clear_calls = 0
        self.icon_files = []

        self._mute_row = self._build_mute_toggle_row()

        self.scope_combo = QComboBox()
        self.scope_combo.addItem("Specific Session", "current")
        self.scope_combo.addItem("Any Session", "any")
        self.scope_combo.setCurrentIndex(1)
        self.scope_combo.currentIndexChanged.connect(self._on_scope_changed)
        self.session_search_input = QLineEdit()
        self._scope_label = QLabel()
        self._detached_scope_combo = None
        self._search_input_shows_session = False
        self._selected_session_id = None

    # Real code under test calls these; the views they touch do not exist here.
    def _clear_transcription_view(self):
        self.clear_calls += 1

    def _load_transcripts_for_session(self, session_id, allow_live=False):
        self.loaded_sessions.append(session_id)

    def _make_icon(self, filename):
        from PySide6.QtGui import QIcon
        self.icon_files.append(filename)
        return QIcon()

    def faces(self):
        """The icon filename on each mute toggle, in _MUTE_TOGGLES order."""
        return self.icon_files[-len(_MUTE_TOGGLES):]

    def prefs(self) -> dict:
        return self._read_preferences()


class HarnessCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = self._tmp.name

    def harness(self, prefs=None, sessions=()):
        h = PrefsHarness(self.tmpdir, sessions=sessions)
        if prefs is not None:
            h._write_preferences(prefs)
        return h


class PreferencesFileTest(HarnessCase):
    def test_a_write_round_trips(self):
        h = self.harness()
        self.assertTrue(h._write_preferences({'a': 1}))
        self.assertEqual(h.prefs(), {'a': 1})

    def test_update_merges_onto_what_is_there(self):
        h = self.harness({'a': 1})
        h._update_preferences({'b': 2})
        self.assertEqual(h.prefs(), {'a': 1, 'b': 2})

    def test_a_write_leaves_no_temp_file_behind(self):
        h = self.harness({'a': 1})
        h._update_preferences({'b': 2})
        leftovers = [n for n in os.listdir(self.tmpdir) if n != 'preferences.json']
        self.assertEqual(leftovers, [])

    def test_a_failed_write_leaves_the_old_file_intact(self):
        h = self.harness({'keep': 'me'})
        with mock.patch('json.dump', side_effect=OSError('disk full')):
            self.assertFalse(h._write_preferences({'lost': True}))
        # The half-written temp file never replaced the real one.
        self.assertEqual(h.prefs(), {'keep': 'me'})
        leftovers = [n for n in os.listdir(self.tmpdir) if n != 'preferences.json']
        self.assertEqual(leftovers, [])

    def test_a_corrupt_file_reads_as_defaults(self):
        h = self.harness()
        with open(h._get_preferences_path(), 'w') as f:
            f.write('{"truncated": ')
        self.assertEqual(h.prefs(), {})

    def test_a_non_object_file_reads_as_defaults(self):
        h = self.harness()
        with open(h._get_preferences_path(), 'w') as f:
            json.dump([1, 2, 3], f)
        self.assertEqual(h.prefs(), {})


class SelectedModelTest(HarnessCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(set_selected_model, DEFAULT_MODEL)

    def test_a_stored_model_is_restored(self):
        from src.config import ALLOWED_MODELS
        other = next(m for m in ALLOWED_MODELS if m != DEFAULT_MODEL)
        h = self.harness({'selected_model': other})
        h._restore_selected_model(h.prefs())
        self.assertEqual(get_selected_model(), other)

    def test_an_unknown_model_falls_back(self):
        set_selected_model(DEFAULT_MODEL)
        h = self.harness({'selected_model': 'acme/does-not-exist'})
        h._restore_selected_model(h.prefs())
        self.assertEqual(get_selected_model(), DEFAULT_MODEL)

    def test_no_stored_model_changes_nothing(self):
        set_selected_model(DEFAULT_MODEL)
        h = self.harness({})
        h._restore_selected_model(h.prefs())
        self.assertEqual(get_selected_model(), DEFAULT_MODEL)


class TranscriptFilterTest(HarnessCase):
    def test_a_stored_filter_is_restored(self):
        h = self.harness({'transcription_filter': 'mic'})
        h._restore_transcription_filter(h.prefs())
        self.assertEqual(h._transcription_filter, 'mic')

    def test_an_unknown_filter_falls_back_to_all(self):
        h = self.harness({'transcription_filter': 'headset'})
        h._restore_transcription_filter(h.prefs())
        self.assertEqual(h._transcription_filter, 'all')

    def test_saving_writes_the_current_filter(self):
        h = self.harness()
        h._transcription_filter = 'system'
        h._save_transcription_filter()
        self.assertEqual(h.prefs()['transcription_filter'], 'system')


class MutePersistenceTest(HarnessCase):
    def test_a_toggle_is_written_to_disk(self):
        h = self.harness()
        h._mute_buttons['mic'].click()
        self.assertEqual(h.prefs()['muted_sources'], {'mic': True, 'system': False})

    def test_a_stored_mute_is_applied_and_announced(self):
        h = self.harness({'muted_sources': {'mic': True, 'system': False}})
        h._restore_muted_sources()

        self.assertTrue(h.session_manager.is_source_muted('mic'))
        self.assertFalse(h.session_manager.is_source_muted('system'))
        self.assertTrue(h._muted_sources['mic'])
        self.assertEqual(h.faces()[0], 'icon_mic_off.svg')
        self.assertIn('Microphone', h.status_messages[-1][0])

    def test_both_muted_are_named_in_one_line(self):
        h = self.harness({'muted_sources': {'mic': True, 'system': True}})
        h._restore_muted_sources()
        message = h.status_messages[-1][0]
        self.assertIn('Microphone', message)
        self.assertIn('System audio', message)

    def test_nothing_muted_says_nothing(self):
        h = self.harness({'muted_sources': {'mic': False, 'system': False}})
        h._restore_muted_sources()
        self.assertEqual(h.status_messages, [])

    def test_a_malformed_value_reads_as_unmuted(self):
        for bad in ('yes', ['mic'], 7, None):
            h = self.harness({'muted_sources': bad})
            self.assertEqual(
                h.stored_muted_sources(),
                {source: False for source, *_rest in _MUTE_TOGGLES},
            )

    def test_unknown_sources_and_values_are_coerced(self):
        h = self.harness({'muted_sources': {'mic': 'true', 'speaker': True}})
        self.assertEqual(h.stored_muted_sources(), {'mic': True, 'system': False})


class SessionScopeTest(HarnessCase):
    SESSIONS = [{'id': 7, 'name': 'Class 1 - RM P2'}]

    def test_the_scope_and_session_are_saved(self):
        h = self.harness(sessions=self.SESSIONS)
        h._selected_session_id = 7
        h.scope_combo.setCurrentIndex(0)  # Specific Session
        h._update_scope_label()

        self.assertEqual(h.prefs()['assistant_scope'], 'current')
        self.assertEqual(h.prefs()['selected_session_id'], 7)

    def test_an_unchanged_selection_is_not_rewritten(self):
        h = self.harness(sessions=self.SESSIONS)
        h._selected_session_id = 7
        h._update_scope_label()
        with mock.patch.object(PrefsHarness, '_update_preferences') as write:
            h._update_scope_label()
            h._update_scope_label()
            write.assert_not_called()

    def test_a_stored_session_comes_back(self):
        h = self.harness({'assistant_scope': 'current', 'selected_session_id': 7},
                         sessions=self.SESSIONS)
        h._restore_session_scope()

        self.assertEqual(h._selected_session_id, 7)
        self.assertEqual(h.scope_combo.currentData(), 'current')
        self.assertEqual(h.session_search_input.text(), 'Class 1 - RM P2')
        self.assertTrue(h._search_input_shows_session)
        self.assertEqual(h.loaded_sessions, [7])  # loaded exactly once

    def test_a_deleted_session_falls_back_to_any_session(self):
        h = self.harness({'assistant_scope': 'current', 'selected_session_id': 404},
                         sessions=self.SESSIONS)
        h._restore_session_scope()

        self.assertIsNone(h._selected_session_id)
        self.assertEqual(h.scope_combo.currentData(), 'any')
        self.assertEqual(h.loaded_sessions, [])
        self.assertEqual(h.status_messages, [])  # nothing the user can act on

    def test_a_garbage_stored_id_falls_back(self):
        h = self.harness({'assistant_scope': 'current', 'selected_session_id': 'seven'},
                         sessions=self.SESSIONS)
        h._restore_session_scope()
        self.assertIsNone(h._selected_session_id)
        self.assertEqual(h.scope_combo.currentData(), 'any')

    def test_restoring_does_not_write_back(self):
        h = self.harness({'assistant_scope': 'current', 'selected_session_id': 7},
                         sessions=self.SESSIONS)
        with mock.patch.object(PrefsHarness, '_update_preferences') as write:
            h._restore_session_scope()
            write.assert_not_called()

    def test_the_chat_is_never_restored(self):
        h = self.harness({'assistant_scope': 'current', 'selected_session_id': 7,
                          'current_conversation_id': 42},
                         sessions=self.SESSIONS)
        h._restore_session_scope()
        h._restore_muted_sources()
        self.assertIsNone(getattr(h, '_current_conversation_id', None))


if __name__ == '__main__':
    unittest.main()
