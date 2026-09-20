import unittest

from services.audio import _normalize_result


class AudioNormalizationTests(unittest.TestCase):
    def test_keeps_raw_text_and_records_configured_corrections(self):
        payload = {'Data': {
            'AudioDuration': 12.5,
            'ResultDetail': [{
                'FinalSentence': '现场要做洲际入侵和高空抛抛物。',
                'StartMs': 1000,
                'EndMs': 4200,
                'SpeakerId': 1,
            }],
        }}
        rules = [
            {'from': '洲际入侵', 'to': '周界入侵', 'mode': 'literal', 'enabled': True},
            {'from': '高空抛抛物', 'to': '高空抛物', 'mode': 'literal', 'enabled': True},
        ]
        raw, corrected, segments, hits, duration = _normalize_result(payload, rules)
        self.assertIn('洲际入侵', raw)
        self.assertIn('周界入侵', corrected)
        self.assertEqual(segments[0]['raw_text'], raw)
        self.assertEqual(segments[0]['text'], corrected)
        self.assertEqual(len(hits), 2)
        self.assertEqual(duration, 12.5)


if __name__ == '__main__':
    unittest.main()
