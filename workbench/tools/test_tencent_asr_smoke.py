import json
import tempfile
import unittest
from pathlib import Path

from tools import tencent_asr_smoke as smoke


class SettingsTests(unittest.TestCase):
    def test_load_settings(self):
        cfg = smoke.load_settings({
            "TENCENTCLOUD_SECRET_ID": "id",
            "TENCENTCLOUD_SECRET_KEY": "key",
            "TENCENT_COS_BUCKET": "lingshi-asr-1250000000",
            "TENCENT_COS_REGION": "ap-shanghai",
        })
        self.assertEqual(cfg.cos_prefix, "asr-mvp")
        self.assertEqual(cfg.bucket, "lingshi-asr-1250000000")

    def test_missing_secret_is_rejected(self):
        with self.assertRaises(smoke.SmokeTestError):
            smoke.load_settings({})

    def test_invalid_bucket_is_rejected(self):
        with self.assertRaises(smoke.SmokeTestError):
            smoke.load_settings({
                "TENCENTCLOUD_SECRET_ID": "id",
                "TENCENTCLOUD_SECRET_KEY": "key",
                "TENCENT_COS_BUCKET": "short-name",
                "TENCENT_COS_REGION": "ap-shanghai",
            })


class OutputTests(unittest.TestCase):
    def test_timestamp_format(self):
        self.assertEqual(smoke.format_timestamp(3_661_234), "01:01:01.234")

    def test_write_outputs(self):
        payload = {
            "Data": {
                "TaskId": 123,
                "Status": 2,
                "StatusStr": "success",
                "AudioDuration": 2.5,
                "Result": "[0:0.020,0:2.380] 完整结果",
                "ResultDetail": [{
                    "FinalSentence": "你好。",
                    "StartMs": 20,
                    "EndMs": 2380,
                    "SpeakerId": 0,
                }],
            }
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            outputs = smoke.write_outputs(root, payload)
            self.assertEqual(outputs["text"].read_text(encoding="utf-8"), "你好。\n")
            self.assertIn(
                "[0:0.020,0:2.380]",
                outputs["provider_text"].read_text(encoding="utf-8"),
            )
            markdown = outputs["markdown"].read_text(encoding="utf-8")
            self.assertIn("00:00:00.020", markdown)
            self.assertIn("说话人 0", markdown)
            self.assertEqual(
                json.loads(outputs["segments"].read_text(encoding="utf-8"))[0]["FinalSentence"],
                "你好。",
            )


if __name__ == "__main__":
    unittest.main()
