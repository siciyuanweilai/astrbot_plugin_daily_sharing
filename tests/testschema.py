import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ConfigSchemaTests(unittest.TestCase):
    def test_weixin_image_size_config_uses_runtime_key(self):
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        image_items = schema["image_conf"]["items"]

        self.assertIn("weixin_image_max_size_kb", image_items)
        self.assertNotIn("weixin_image_max_size_mb", image_items)

    def test_news_image_cleanup_config_exists(self):
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        image_items = schema["image_conf"]["items"]

        self.assertIn("news_image_cleanup_max_count", image_items)
        self.assertEqual(image_items["news_image_cleanup_max_count"]["default"], 200)

    def test_dashboard_dynamic_days_config_exists(self):
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        basic_items = schema["basic_conf"]["items"]

        self.assertIn("dashboard_dynamic_days", basic_items)
        self.assertEqual(basic_items["dashboard_dynamic_days"]["default"], 60)

    def test_runtime_does_not_read_legacy_weixin_image_size_key(self):
        runtime = (ROOT / "core" / "tasks" / "delivery.py").read_text(encoding="utf-8")

        self.assertNotRegex(runtime, re.escape("weixin_image_max_size_mb"))


if __name__ == "__main__":
    unittest.main()
