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


class Phase1SmokeTest(unittest.TestCase):
    def test_feedback_loop_smoke(self):
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
        }
        agent.context = {
            "previous_actions": [],
            "current_app": "Home",
            "task_request": "",
            "continuous_task": False,
            "task_started_at": None,
            "session_id": "smoke",
            "screenshots": [],
            "stop_requested": False,
            "retry_decisions": [],
            "retry_round": 0,
            "last_retry_reason": None,
        }

        class DummyVL:
            def __init__(self):
                self.calls = 0

            def analyze_screenshot(self, screenshot_path, user_request, context, retry_feedback=None, **kwargs):
                self.calls += 1
                if retry_feedback is None:
                    return {"action": "tap", "coordinates": [0.5, 0.5], "observation": "tap center"}
                # correction must differ from failed tap
                return {"action": "wait", "waitTime": 10, "observation": "wait for load"}

            def check_task_completion(self, screenshot_path, user_request, context):
                return {"complete": True, "reason": "done"}

        agent.vl_agent = DummyVL()

        shot_idx = {"n": 0}

        def fake_capture():
            shot_idx["n"] += 1
            path = f"/tmp/fake_{shot_idx['n']}.png"
            agent.context["screenshots"].append(path)
            return path

        agent.capture_screenshot = fake_capture

        # First execution fails, correction succeeds, then completion check marks complete
        exec_calls = {"n": 0}

        def fake_execute(action):
            exec_calls["n"] += 1
            if exec_calls["n"] == 1:
                return {
                    "success": False,
                    "error": "Tap action missing coordinates",
                    "action": action,
                    "task_complete": False,
                }
            return {
                "success": True,
                "action": action,
                "task_complete": False,
            }

        agent.execute_action = fake_execute

        res = PhoneAgent.execute_task(agent, "打开相机", max_cycles=1)

        self.assertTrue(res["success"])
        self.assertTrue(res["task_complete"])
        self.assertGreaterEqual(len(res["context"].get("retry_decisions", [])), 1)
        self.assertEqual(res["context"].get("last_retry_reason"), "坐标偏差")


if __name__ == "__main__":
    unittest.main(verbosity=2)
