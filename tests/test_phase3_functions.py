import sys
import types
import unittest


# Stub heavy dependency chain before importing phone_agent
fake_qwen = types.ModuleType("qwen_vl_agent")


class _DummyQwenVLAgent:
    pass


fake_qwen.QwenVLAgent = _DummyQwenVLAgent
sys.modules["qwen_vl_agent"] = fake_qwen

from phone_agent import PhoneAgent  # noqa: E402


class Phase3FunctionTests(unittest.TestCase):
    def _new_agent(self):
        agent = object.__new__(PhoneAgent)
        agent.config = {
            "enable_exception_handler": True,
            "hitl_on_captcha": True,
            "exception_network_backoff_ms": 2000,
        }
        agent.context = {
            "session_id": "phase3-func",
            "previous_actions": [],
            "exception_events": [],
            "last_exception_type": None,
            "last_handler_action": None,
            "last_hitl_triggered": False,
        }
        return agent

    def test_exception_classification_priority(self):
        agent = self._new_agent()
        agent._extract_text_tokens = lambda _path: ["请完成验证码", "允许权限"]

        exception_type = PhoneAgent._detect_ui_exception(agent, "/tmp/s.png")
        self.assertEqual(exception_type, "captcha_entry")

    def test_strategy_selection_hitl_and_popup(self):
        agent = self._new_agent()

        captcha_strategy = PhoneAgent._select_exception_strategy(agent, "captcha_entry")
        self.assertEqual(captcha_strategy["mode"], "hitl")
        self.assertTrue(captcha_strategy["hitl"])
        self.assertEqual(captcha_strategy["action"]["action"], "terminate")
        self.assertEqual(captcha_strategy["action"]["status"], "failure")

        popup_strategy = PhoneAgent._select_exception_strategy(agent, "permission_popup")
        self.assertEqual(popup_strategy["mode"], "blocking_popup")
        self.assertFalse(popup_strategy["hitl"])
        self.assertEqual(popup_strategy["action"]["action"], "tap")


if __name__ == "__main__":
    unittest.main(verbosity=2)
