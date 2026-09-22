"""直接手填、文字稿往返切换及人工确认优先级的回归测试。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as web
from core import db, paths, repo
from core.config import field_map
from core.validate import validate
from services import survey


class InputModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_conn = getattr(db._local, 'conn', None)
        db._local.conn = None
        self.db_patch = patch.object(db, 'db_path', return_value=str(Path(self.tmp.name) / 'test.db'))
        self.db_patch.start()
        self.client = web.app.test_client()
        with self.client.session_transaction() as session:
            session['dept_key'] = 'dept1'
        self.project = repo.create_project('dept1', {'name': '自由切换测试'})

    def tearDown(self):
        if getattr(db._local, 'conn', None):
            db._local.conn.close()
        db._local.conn = self.original_conn
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_blank_form_is_accessible_and_counts_missing_for_both_products(self):
        for product_type in ('safety_butler', 'fire_butler'):
            with self.subTest(product_type=product_type):
                pp = repo.ensure_product(self.project['id'], product_type)
                response = self.client.get(f"/p/{pp['id']}/review")
                self.assertEqual(response.status_code, 200)
                self.assertIn('直接', self.client.get(f"/p/{pp['id']}/survey").get_data(as_text=True))
                stats = repo.field_stats(pp['id'], product_type)
                self.assertGreater(stats['required_total'], 0)
                self.assertEqual(stats['required_missing'], stats['required_total'])
                self.assertEqual(response.get_data(as_text=True).count('data-status="required_missing"'), stats['required_total'])
                self.assertIsNone(repo.latest_source(pp['id']))
                self.assertIsNone(repo.latest_extraction_run(pp['id']))
                key = next(k for k, f in field_map(product_type).items() if f['type'] == 'text')
                saved = self.client.post(f"/p/{pp['id']}/field", json={'key': key, 'value': '现场确认'})
                self.assertEqual(saved.json['status'], 'confirmed')
                self.assertEqual(repo.field_values(pp['id'])[key]['protected'], 1)
                self.assertEqual(repo.progress_of(pp['id'], product_type)[1], 'pending_fill')

    def test_manual_only_can_reach_preview_without_source_or_extraction(self):
        pp = repo.ensure_product(self.project['id'], 'fire_butler')
        for key, f in field_map('fire_butler').items():
            value = 4 if f['type'] == 'number' else (
                f['options'][0]['value'] if f['type'] == 'select' else (
                [f['options'][0]['value']] if f['type'] == 'multiselect' else '现场已确认'))
            response = self.client.post(f"/p/{pp['id']}/field", json={'key': key, 'value': value})
            self.assertEqual(response.status_code, 200)
        self.assertTrue(validate(pp['id'], 'fire_butler')[0])
        self.assertEqual(self.client.get(f"/p/{pp['id']}/preview").status_code, 200)
        self.assertIsNone(repo.latest_source(pp['id']))
        self.assertIsNone(repo.latest_extraction_run(pp['id']))

    def test_import_and_repeated_extraction_preserve_manual_values(self):
        pp = repo.ensure_product(self.project['id'], 'fire_butler')
        text = (Path(paths.SAMPLES_DIR) / 'fire_butler/full.md').read_text(encoding='utf-8')
        survey.save_field_value(pp, self.project, {'key': 'fire_renovation_cost', 'value': 12345})
        repo.save_project_transcript(self.project['id'], '', text)
        response = self.client.post(f"/p/{pp['id']}/import-transcript")
        self.assertEqual(response.status_code, 302)
        survey.extract_latest_source(pp, self.project, provider='rule')
        self.client.post(f"/p/{pp['id']}/survey", data={'transcript': text})
        survey.extract_latest_source(pp, self.project, provider='rule')
        row = repo.field_values(pp['id'])['fire_renovation_cost']
        self.assertEqual(json.loads(row['value_json']), 12345)
        self.assertEqual((row['status'], row['protected'], row['updated_by']), ('confirmed', 1, 'human'))
        self.assertTrue(any(v['updated_by'] == 'ai' for v in repo.field_values(pp['id']).values()))

    def test_human_confirmation_during_extraction_is_preserved(self):
        pp = repo.ensure_product(self.project['id'], 'fire_butler')
        text = (Path(paths.SAMPLES_DIR) / 'fire_butler/full.md').read_text(encoding='utf-8')
        survey.save_transcript(pp, self.project, text)
        original = survey.extract.run_extraction

        def extract_while_editing(*args, **kwargs):
            result = original(*args, **kwargs)
            survey.save_field_value(pp, self.project, {'key': 'fire_renovation_cost', 'value': 67890})
            return result

        with patch.object(survey.extract, 'run_extraction', side_effect=extract_while_editing):
            _, protected = survey.extract_latest_source(pp, self.project, provider='rule')
        self.assertEqual(protected, 1)
        self.assertEqual(json.loads(repo.field_values(pp['id'])['fire_renovation_cost']['value_json']), 67890)

    def test_transcription_offers_manual_entry_in_every_status(self):
        pp = repo.ensure_product(self.project['id'], 'fire_butler')
        job = repo.create_audio_job(self.project['id'], 'test.mp3', '/unused/test.mp3', 123)
        for status in ('pending', 'transcribing', 'failed', 'awaiting_preview', 'approved'):
            repo.update_audio_job(job['id'], status=status)
            with patch.object(web.audio_service, 'start_job'):
                response = self.client.get(f"/projects/{self.project['id']}/audio/{job['id']}")
            self.assertEqual(response.status_code, 200)
            self.assertIn(f"/p/{pp['id']}/review", response.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
