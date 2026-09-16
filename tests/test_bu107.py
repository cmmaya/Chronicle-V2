"""BU107: two-tier screenshot search engine."""
import json
import unittest

from src.screenshots.search import (
    MAX_FULL_DETAILS,
    normalize_tokens,
    promote,
    rank_previews,
    referenced_ids,
    search_session_screenshots,
)

T0 = 1_700_000_000


def _row(id_, offset, preview='', keywords=None, description=None, **full):
    row = {
        'id': id_, 'session_id': 1, 'timestamp': T0 + offset,
        'filepath': f'/s/{id_}.png', 'description': description,
        'preview_description': preview, 'preview_source': 'ai',
        'keywords': json.dumps(keywords or []),
    }
    row.update(full)
    return row


class SpyDb:
    """Minimal database: Tier-1 previews plus a spied Tier-2 lookup."""

    def __init__(self, rows):
        self.rows = rows
        self.detail_calls = []

    def get_screenshot_previews(self, session_id):
        return [dict(r) for r in self.rows]

    def get_transcripts(self, session_id):
        return []

    def get_session(self, session_id):
        return {'start_time': T0}

    def update_screenshot_preview(self, *args):
        raise AssertionError('previews are all AI-sourced in these tests')

    def get_screenshot_details(self, ids):
        self.detail_calls.append(list(ids))
        return [dict(r) for r in self.rows if r['id'] in ids]


class TokenizationTest(unittest.TestCase):
    def test_accents_case_and_stop_words(self):
        self.assertEqual(
            normalize_tokens('¿Qué dijeron sobre la Reunión de Presupuesto?'),
            ['reunion', 'presupuesto'],
        )
        self.assertEqual(normalize_tokens('What was said about the dashboard'), ['dashboard'])

    def test_explicit_references(self):
        self.assertEqual(referenced_ids('what is in screenshot #42?'), [42])
        self.assertEqual(referenced_ids('mira la captura 7 y la #9'), [7, 9])
        self.assertEqual(referenced_ids('Screenshot 3 please'), [3])
        self.assertEqual(referenced_ids('no numbers here'), [])


class RankTest(unittest.TestCase):
    def test_lexical_match_wins_and_keywords_weigh_more(self):
        rows = [
            _row(1, 0, 'Grafana dashboard with latency panel'),
            _row(2, 60, 'Slides about hiring plan', keywords=['latency']),
            _row(3, 120, 'Email inbox'),
        ]
        hits = rank_previews(rows, 'what was the latency?')
        self.assertEqual([h.screenshot_id for h in hits[:2]], [2, 1])
        self.assertGreater(hits[0].score, hits[1].score)
        self.assertEqual(hits[2].score, 0)

    def test_plural_and_accent_insensitive_match(self):
        rows = [_row(1, 0, 'Lista de reuniones del equipo'), _row(2, 10, 'Código fuente')]
        hits = rank_previews(rows, 'la reunion y el codigo')
        self.assertEqual({h.screenshot_id for h in hits if 'lexical' in h.reasons}, {1, 2})

    def test_temporal_boost_decays(self):
        rows = [_row(1, 0), _row(2, 150), _row(3, 1000)]
        hits = {h.screenshot_id: h for h in rank_previews(rows, 'budget', [T0 + 30])}
        self.assertIn('temporal', hits[1].reasons)
        self.assertLess(hits[2].score, hits[1].score)
        self.assertGreater(hits[2].score, 0)
        self.assertEqual(hits[3].score, 0)

    def test_explicit_reference_goes_first(self):
        rows = [_row(1, 0, 'budget spreadsheet'), _row(2, 10, 'unrelated')]
        hits = rank_previews(rows, 'budget in screenshot #2')
        self.assertEqual(hits[0].screenshot_id, 2)
        self.assertIn('reference', hits[0].reasons)

    def test_deictic_question_points_at_nearest_or_latest(self):
        rows = [_row(1, 0, 'a'), _row(2, 300, 'b'), _row(3, 900, 'c')]
        near = rank_previews(rows, 'what was on the screen?', [T0 + 310])
        self.assertEqual(near[0].screenshot_id, 2)
        self.assertIn('deictic', near[0].reasons)
        latest = rank_previews(rows, 'que se ve en la captura?')
        self.assertEqual(latest[0].screenshot_id, 3)


class PromoteTest(unittest.TestCase):
    def test_threshold_blocks_weak_matches(self):
        rows = [_row(1, 0, 'a'), _row(2, 500, 'b')]
        db = SpyDb(rows)
        hits = rank_previews(rows, 'budget', [T0 + 10])  # temporal only
        self.assertEqual(promote(hits, db), [])
        self.assertEqual(db.detail_calls, [])

    def test_details_loaded_once_for_winners_only(self):
        rows = [
            _row(i, i * 400, f'budget review part {i}', ai_summary=f'sum {i}',
                 visible_text=json.dumps(['Total: 4M', 'Q3']))
            for i in range(1, 6)
        ]
        rows.append(_row(9, 5000, 'holiday photos'))
        db = SpyDb(rows)
        promoted = promote(rank_previews(rows, 'budget review'), db)
        self.assertEqual(len(db.detail_calls), 1)
        self.assertEqual(len(db.detail_calls[0]), MAX_FULL_DETAILS)
        self.assertNotIn(9, db.detail_calls[0])
        self.assertTrue(all(h.tier == 'full' for h in promoted))
        self.assertEqual(promoted[0].details['visible_text'], ['Total: 4M', 'Q3'])


class SearchSessionTest(unittest.TestCase):
    def test_empty_session(self):
        self.assertEqual(search_session_screenshots(SpyDb([]), 1, 'anything'), [])

    def test_full_first_then_index_in_capture_order_with_cap(self):
        rows = [_row(i, i * 400, f'note {i}') for i in range(1, 21)]
        rows[4]['preview_description'] = 'Kubernetes pod crash loop'
        db = SpyDb(rows)
        hits = search_session_screenshots(db, 1, 'why did the pod crash?', max_preview=6)
        self.assertEqual(len(hits), 6)
        self.assertEqual(hits[0].screenshot_id, 5)
        self.assertEqual(hits[0].tier, 'full')
        index = hits[1:]
        self.assertTrue(all(h.tier == 'preview' for h in index))
        self.assertEqual([h.timestamp for h in index], sorted(h.timestamp for h in index))
        # The cap keeps the most recent screenshots when nothing else matches.
        self.assertIn(20, [h.screenshot_id for h in index])

    def test_minimal_fake_without_tier_methods(self):
        class LegacyDb:
            def get_screenshots(self, session_id):
                return [_row(1, 0, description='login error page',
                             ai_summary='500 on login')]

        hits = search_session_screenshots(LegacyDb(), 1, 'login error')
        self.assertEqual(hits[0].tier, 'full')
        self.assertEqual(hits[0].details['ai_summary'], '500 on login')


if __name__ == '__main__':
    unittest.main()
