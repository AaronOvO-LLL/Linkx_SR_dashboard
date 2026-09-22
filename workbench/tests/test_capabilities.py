import unittest

from core.capabilities import has_capability, validate_capabilities


class CapabilityTests(unittest.TestCase):
    def test_safety_butler_declares_current_workflow(self):
        self.assertTrue(has_capability('safety_butler', 'text_input'))
        self.assertTrue(has_capability('safety_butler', 'field_extraction'))
        self.assertTrue(has_capability('safety_butler', 'artifact_generation'))

    def test_audio_asr_gates_project_transcript_import(self):
        """audio_asr 现在表示"可导入项目级转写稿"，上传与转写本身不受产品能力约束。"""
        self.assertTrue(has_capability('safety_butler', 'audio_asr'))
        self.assertFalse(has_capability('safety_butler', 'image_evidence'))
        self.assertEqual(validate_capabilities('safety_butler'), [])


if __name__ == '__main__':
    unittest.main()
