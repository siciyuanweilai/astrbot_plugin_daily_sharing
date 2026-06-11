import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class _Logger:
    def debug(self, *args, **kwargs):
        return None

    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None


def _install_astrbot_stub():
    for name in list(sys.modules):
        if name.startswith("astrbot"):
            sys.modules.pop(name, None)

    astrbot = types.ModuleType("astrbot")
    astrbot.__path__ = []
    astrbot_api = types.ModuleType("astrbot.api")
    astrbot_api.__path__ = []
    astrbot_api.logger = _Logger()

    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = astrbot_api


def _install_config_stub():
    package_name = "daily_sharing_image_prompt_test"
    image_package_name = f"{package_name}.image"
    package = types.ModuleType(package_name)
    package.__path__ = [str(ROOT / "core")]
    image_package = types.ModuleType(image_package_name)
    image_package.__path__ = [str(ROOT / "core" / "image")]
    sys.modules[package_name] = package
    sys.modules[image_package_name] = image_package

    config_module = types.ModuleType("daily_sharing_image_prompt_test.config")

    class SharingType:
        GREETING = types.SimpleNamespace(value="greeting")
        MOOD = types.SimpleNamespace(value="mood")
        NEWS = types.SimpleNamespace(value="news")
        RECOMMENDATION = types.SimpleNamespace(value="recommendation")

    class TimePeriod:
        DAWN = "dawn"
        MORNING = "morning"
        FORENOON = "forenoon"
        NOON = "noon"
        AFTERNOON = "afternoon"
        EVENING = "evening"
        NIGHT = "night"
        LATE_NIGHT = "late_night"

    config_module.SharingType = SharingType
    config_module.TimePeriod = TimePeriod
    sys.modules["daily_sharing_image_prompt_test.config"] = config_module
    return package_name, SharingType, TimePeriod


def _load_prompt_module():
    _install_astrbot_stub()
    package_name, sharing_type, time_period = _install_config_stub()
    module_name = f"{package_name}.image.prompt"
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "core" / "image" / "prompt.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module, sharing_type, time_period


class ImagePromptTests(unittest.TestCase):
    def test_final_prompt_sanitizes_sensitive_outfit_words(self):
        prompt_module, sharing_type, time_period = _load_prompt_module()

        class Service(prompt_module.ImageVisualMixin):
            img_conf = {"use_gitee_selfie_ref": True}

            def _get_current_period(self):
                return time_period.FORENOON

        service = Service()
        visuals = {
            "outfit": "浅杏色无痕内衣裤作为内搭，薄荷绿真丝吊带裙外穿，裸色短袜",
            "environment": "市集入口",
            "lighting": "上午自然光",
            "weather_vibe": "晴朗海风",
        }

        result = asyncio.run(
            service._assemble_final_prompt(
                "早安",
                sharing_type.GREETING,
                True,
                visuals,
            )
        )

        for blocked in ("内搭", "内衣", "内裤", "吊带", "裸色"):
            self.assertNotIn(blocked, result)
        self.assertIn("真丝连衣裙", result)
        self.assertIn("浅杏色", result)


if __name__ == "__main__":
    unittest.main()
