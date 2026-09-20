import unittest

from core.capabilities import has_capability, validate_capabilities


class CapabilityTests(unittest.TestCase):
    def test_safety_butler_declares_current_workflow(self):
        self.assertTrue(has_capability('safety_butler', 'text_input'))
        self.assertTrue(has_capability('safety_butler', 'field_extraction'))
        self.assertTrue(has_capability('safety_butler', 'artifact_generation'))

    def test_audio_asr_is_enabled_after_provider_integration(self):
        self.assertTrue(has_capability('safety_butler', 'audio_asr'))
        self.assertFalse(has_capability('safety_butler', 'image_evidence'))
        self.assertEqual(validate_capabilities('safety_butler'), [])


if __name__ == '__main__':
    unittest.main()
