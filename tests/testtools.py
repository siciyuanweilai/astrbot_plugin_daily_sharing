import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _tool_docstring(function_name: str) -> str:
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8-sig"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == function_name:
            return ast.get_docstring(node) or ""
    raise AssertionError(f"missing tool function: {function_name}")


def _args_from_docstring(docstring: str) -> dict:
    return {
        name: type_name
        for name, type_name in re.findall(
            r"^\s+([A-Za-z_][A-Za-z0-9_]*) \((string|boolean|number|object|array)\):",
            docstring,
            flags=re.MULTILINE,
        )
    }


class LlmToolDocstringTests(unittest.TestCase):
    def test_daily_share_tool_declares_astrbot_args(self):
        args = _args_from_docstring(_tool_docstring("daily_share_tool"))

        self.assertEqual(
            args,
            {
                "share_type": "string",
                "source": "string",
                "get_image": "boolean",
                "need_image": "boolean",
                "need_video": "boolean",
                "need_voice": "boolean",
                "to_qzone": "boolean",
            },
        )

    def test_news_link_tool_declares_astrbot_args(self):
        args = _args_from_docstring(_tool_docstring("news_link_tool"))

        self.assertEqual(
            args,
            {
                "action": "string",
                "index": "string",
                "query": "string",
                "source": "string",
                "to_qzone": "boolean",
            },
        )
