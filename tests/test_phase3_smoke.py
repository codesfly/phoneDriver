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

from phone_agent import PhoneAgent, TaskPlanner  # noqa: E402


class Phase3SmokeTest(unittest.TestCase):
    def _build_agent(self, checkpoint_dir):
        agent = object.__new__(PhoneAgent)
        agent.config = {
            "step_delay": 0,
            "continuous_min_cycles": 1,
            "continuous_min_minutes": 0,
            "ignore_terminate_for_continuous_tasks": False,
            "enable_dynamic_retry_budget": True,
            "retry_budget_simple": 2,
            "retry_budget_medium": 4,
            "retry_budget_complex": 6,
            "retry_budget_cap": 8,
            "max_retries": 3,
            "enable_task_planner": True,
            "planner_max_steps": 8,
            "enable_checkpoint_recovery": True,
            "checkpoint_dir": checkpoint_dir,
            "enable_exception_handler": True,
            "hitl_on_captcha": True,
            "exception_network_backoff_ms": 1200,
        }
        agent.context = {
            "previous_actions": [],
            "current_app": "Home",
            "task_request": "",
            "continuous_task": False,
            "task_started_at": None,
            "session_id": "phase3-smoke",
            "screenshots": [],
            "stop_requested": False,
            "retry_decisions": [],
            "retry_round": 0,
            "last_retry_reason": None,
            "exception_events": [],
            "last_exception_type": None,
            "last_handler_action": None,
            "last_hitl_triggered": False,
        }
        agent.task_planner = TaskPlanner(max_steps=8)
        agent.current_plan = None
        agent.current_step_index = 0
        agent.step_status = {}
        agent.current_checkpoint_path = None

        class DummyVL:
            def analyze_screenshot(self, screenshot_path, user_request, context, retry_feedback=None):
                return {
                    "action": "wait",
                    "waitTime": 300,
                    "observation": "main-flow",
                }

            def check_task_completion(self, screenshot_path, user_request, context):
                return {"complete": False, "reason": "not done"}

        agent.vl_agent = DummyVL()

        idx = {"n": 0}

        def fake_capture():
            idx["n"] += 1
            path = f"/tmp/phase3_smoke_{idx['n']}.png"
            agent.context["screenshots"].append(path)
            return path

        agent.capture_screenshot = fake_capture

        def fake_execute(action):
            return {
                "success": True,
                "action": action,
                "task_complete": action.get("action") == "terminate",
            }

        agent.execute_action = fake_execute
        return agent

    def test_smoke_popup_then_captcha_hitl(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = self._build_agent(tmp)
            detect_seq = iter(["permission_popup", "captcha_entry"])
            agent._detect_ui_exception = lambda _path: next(detect_seq, None)

            res = PhoneAgent.execute_task(agent, "打开应用并继续", max_cycles=3)

            self.assertFalse(res["success"])
            self.assertFalse(res["task_complete"])
            self.assertEqual(res["step_status"].get("0"), "failed")
            self.assertEqual(res["context"].get("last_exception_type"), "captcha_entry")
            self.assertTrue(res["context"].get("last_hitl_triggered"))
            self.assertGreaterEqual(len(res["context"].get("exception_events", [])), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
