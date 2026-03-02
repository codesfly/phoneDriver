import os
import sys
import tempfile
import types
import unittest


# Stub heavy dependency chain before importing phone_agent
fake_qwen = types.ModuleType("qwen_vl_agent")


class _DummyQwenVLAgent:
    pass


fake_qwen.QwenVLAgent = _DummyQwenVLAgent
sys.modules["qwen_vl_agent"] = fake_qwen

from phone_agent import PhoneAgent, parse_wm_size_output  # noqa: E402


class P0FunctionTests(unittest.TestCase):
    def _new_agent(self):
        agent = object.__new__(PhoneAgent)
        agent.config = {
            "screenshot_dir": "/tmp",
            "use_fast_screencap": True,
            "adb_command_timeout": 5,
            "runtime_config_path": "config.json",
            "screen_width": 1080,
            "screen_height": 2340,
        }
        agent.context = {
            "session_id": "p0test",
            "screenshots": [],
            "last_screencap_mode": None,
        }
        return agent

    def test_fast_screenshot_success_path(self):
        agent = self._new_agent()
        calls = {"fast": 0, "legacy": 0}

        def fake_fast(path):
            calls["fast"] += 1
            with open(path, "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\nFAST")

        def fake_legacy(path):
            calls["legacy"] += 1
            with open(path, "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\nLEGACY")

        agent._capture_screenshot_fast = fake_fast
        agent._capture_screenshot_legacy = fake_legacy

        path = PhoneAgent.capture_screenshot(agent)

        self.assertTrue(os.path.exists(path))
        self.assertEqual(calls["fast"], 1)
        self.assertEqual(calls["legacy"], 0)
        self.assertEqual(agent.context.get("last_screencap_mode"), "fast")
        self.assertIn(path, agent.context.get("screenshots", []))
        os.remove(path)

    def test_fast_screenshot_fallback_to_legacy(self):
        agent = self._new_agent()
        calls = {"fast": 0, "legacy": 0}

        def fake_fast(_):
            calls["fast"] += 1
            raise RuntimeError("fast failed")

        def fake_legacy(path):
            calls["legacy"] += 1
            with open(path, "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\nLEGACY")

        agent._capture_screenshot_fast = fake_fast
        agent._capture_screenshot_legacy = fake_legacy

        path = PhoneAgent.capture_screenshot(agent)

        self.assertTrue(os.path.exists(path))
        self.assertEqual(calls["fast"], 1)
        self.assertEqual(calls["legacy"], 1)
        self.assertEqual(agent.context.get("last_screencap_mode"), "legacy_fallback")
        os.remove(path)

    def test_healthcheck_parser_resolution(self):
        parsed = parse_wm_size_output("Physical size: 1080x2400\n")
        self.assertEqual(parsed, (1080, 2400))
        self.assertIsNone(parse_wm_size_output("garbage output"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
