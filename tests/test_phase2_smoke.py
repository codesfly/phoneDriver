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


class Phase2SmokeTest(unittest.TestCase):
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
        }
        agent.context = {
            "previous_actions": [],
            "current_app": "Home",
            "task_request": "",
            "continuous_task": False,
            "task_started_at": None,
            "session_id": "phase2-smoke",
            "screenshots": [],
            "stop_requested": False,
            "retry_decisions": [],
            "retry_round": 0,
            "last_retry_reason": None,
        }
        agent.task_planner = TaskPlanner(max_steps=8)
        agent.current_plan = None
        agent.current_step_index = 0
        agent.step_status = {}
        agent.current_checkpoint_path = None

        class DummyVL:
            def analyze_screenshot(self, screenshot_path, user_request, context, retry_feedback=None, **kwargs):
                # Always return a terminate success for current step
                return {
                    "action": "terminate",
                    "status": "success",
                    "message": "step done",
                    "observation": "ok",
                }

            def check_task_completion(self, screenshot_path, user_request, context):
                return {"complete": True, "reason": "done"}

        agent.vl_agent = DummyVL()

        shot_idx = {"n": 0}

        def fake_capture():
            shot_idx["n"] += 1
            path = f"/tmp/phase2_smoke_{shot_idx['n']}.png"
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

    def test_resume_from_checkpoint_after_interruption(self):
        task = "打开设置，然后打开蓝牙"

        with tempfile.TemporaryDirectory() as tmp:
            # First run: fail at step2 and leave checkpoint.
            agent1 = self._build_agent(tmp)
            original_execute_step_cycles = agent1._execute_step_cycles
            step_counter = {"n": 0}

            def first_run_execute(step_prompt, max_cycles=15):
                step_counter["n"] += 1
                if step_counter["n"] == 1:
                    return {
                        "success": True,
                        "task_complete": True,
                        "cycles": 1,
                        "last_error": None,
                        "last_action": {"action": "terminate", "status": "success"},
                        "last_screenshot": "/tmp/step1.png",
                    }
                # simulate interruption/failure at step2
                return {
                    "success": False,
                    "task_complete": False,
                    "cycles": 1,
                    "last_error": "interrupted",
                    "last_action": {"action": "wait", "waitTime": 1000},
                    "last_screenshot": "/tmp/step2.png",
                }

            agent1._execute_step_cycles = first_run_execute
            res1 = PhoneAgent.execute_task(agent1, task, max_cycles=2)
            self.assertFalse(res1["task_complete"])
            self.assertFalse(res1["success"])
            self.assertEqual(res1["current_step_index"], 1)
            self.assertEqual(res1["step_status"].get("0"), "done")
            self.assertEqual(res1["step_status"].get("1"), "failed")
            self.assertTrue(res1["checkpoint_path"])

            # Second run: recover from checkpoint and finish remaining step.
            agent2 = self._build_agent(tmp)
            resume_counter = {"n": 0}

            def second_run_execute(step_prompt, max_cycles=15):
                resume_counter["n"] += 1
                return {
                    "success": True,
                    "task_complete": True,
                    "cycles": 1,
                    "last_error": None,
                    "last_action": {"action": "terminate", "status": "success"},
                    "last_screenshot": "/tmp/step2_done.png",
                }

            agent2._execute_step_cycles = second_run_execute
            res2 = PhoneAgent.execute_task(agent2, task, max_cycles=2)

            self.assertTrue(res2["success"])
            self.assertTrue(res2["task_complete"])
            self.assertEqual(res2["current_step_index"], 2)
            self.assertEqual(res2["step_status"].get("0"), "done")
            self.assertEqual(res2["step_status"].get("1"), "done")
            self.assertEqual(resume_counter["n"], 1, "resume should only execute remaining step")


if __name__ == "__main__":
    unittest.main(verbosity=2)
