import json
import tempfile
import unittest
from pathlib import Path

from tools import tencent_asr_hotwords as hotwords
from tools.tencent_asr_smoke import SmokeTestError


class HotwordValidationTests(unittest.TestCase):
    def test_checked_in_config_matches_generated_file(self):
        config = hotwords.load_config(hotwords.DEFAULT_CONFIG)
        rendered = hotwords.render_hotwords(config)
        hotwords.validate_generated_file(hotwords.DEFAULT_CONFIG, config, rendered)
        self.assertEqual(len(config["hotwords"]), 46)
        self.assertNotIn("|100", rendered)

    def test_duplicate_is_rejected(self):
        config = {
            "provider": {"engine_model_type": "16k_zh_en_2.0"},
            "hotwords": [
                {"term": "消控室", "weight": 10},
                {"term": "消控室", "weight": 11},
            ],
        }
        with self.assertRaises(SmokeTestError):
            hotwords.render_hotwords(config)

    def test_weight_100_is_rejected_for_mixed_engine(self):
        config = {
            "provider": {"engine_model_type": "16k_zh_en_2.0"},
            "hotwords": [{"term": "周界入侵", "weight": 100}],
        }
        with self.assertRaises(SmokeTestError):
            hotwords.render_hotwords(config)

    def test_generated_file_drift_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = root / "asr_terms.json"
            generated_path = root / "hotwords.txt"
            config = {
                "provider": {
                    "engine_model_type": "16k_zh_en_2.0",
                    "generated_file": generated_path.name,
                },
                "hotwords": [{"term": "踏勘", "weight": 11}],
            }
            config_path.write_text(
                json.dumps(config, ensure_ascii=False), encoding="utf-8"
            )
            generated_path.write_text("错词|1\n", encoding="utf-8")
            with self.assertRaises(SmokeTestError):
                hotwords.validate_generated_file(
                    config_path, config, hotwords.render_hotwords(config)
                )


if __name__ == "__main__":
    unittest.main()
