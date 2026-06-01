import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "core" / "command_args.py"

SHARE = "/\u5206\u4eab"
RECOMMEND = "\u63a8\u8350"
AUTO = "\u81ea\u52a8"
BROADCAST = "\u5e7f\u64ad"
QZONE = "\u7a7a\u95f4"
COMPUTER = "\u7535\u8111"


def _load_module():
    spec = importlib.util.spec_from_file_location("command_args_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CommandArgsTests(unittest.TestCase):
    def test_rejects_unknown_non_news_suffix(self):
        mod = _load_module()

        invalid = mod.find_invalid_non_news_args([SHARE, RECOMMEND, COMPUTER])

        self.assertEqual(invalid, [COMPUTER])

    def test_accepts_broadcast_and_qzone_suffixes(self):
        mod = _load_module()

        self.assertEqual(
            mod.find_invalid_non_news_args([SHARE, RECOMMEND, BROADCAST]),
            [],
        )
        self.assertEqual(
            mod.find_invalid_non_news_args([SHARE, RECOMMEND, QZONE]),
            [],
        )

    def test_returns_only_unknown_suffixes(self):
        mod = _load_module()

        invalid = mod.find_invalid_non_news_args(
            [SHARE, RECOMMEND, COMPUTER, BROADCAST]
        )

        self.assertEqual(invalid, [COMPUTER])

    def test_applies_to_auto_share_arguments_too(self):
        mod = _load_module()

        invalid = mod.find_invalid_non_news_args([SHARE, AUTO, COMPUTER])

        self.assertEqual(invalid, [COMPUTER])


if __name__ == "__main__":
    unittest.main()
