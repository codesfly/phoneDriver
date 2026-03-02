import json
import sys
import tempfile
import types
import unittest
from pathlib import Path


# Stub heavy dependency chain before importing phone_agent
fake_qwen = types.ModuleType("qwen_vl_agent")


class _DummyQwenVLAgent:
    pass


fake_qwen.QwenVLAgent = _DummyQwenVLAgent
sys.modules["qwen_vl_agent"] = fake_qwen

from phone_agent import PhoneAgent, TaskPlanner  # noqa: E402


class Phase2FunctionTests(unittest.TestCase):
    def test_task_planner_json_structure(self):
        planner = TaskPlanner(max_steps=6)
        plan = planner.build_plan("打开抖音，然后搜索旅行视频，并且点赞第一个视频")

        self.assertIn("steps", plan)
        self.assertIsInstance(plan["steps"], list)
        self.assertGreaterEqual(len(plan["steps"]), 2)

        for step in plan["steps"]:
            self.assertIsInstance(step, dict)
            self.assertTrue(step.get("step_name"))
            self.assertTrue(step.get("instruction"))
            self.assertTrue(step.get("success_criteria"))

    def test_checkpoint_save_and_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = object.__new__(PhoneAgent)
            agent.config = {
                "enable_checkpoint_recovery": True,
                "checkpoint_dir": tmp,
            }
            agent.context = {
                "task_request": "打开设置然后打开蓝牙",
                "last_action": {"action": "tap", "coordinates": [0.2, 0.3]},
                "last_screenshot": "/tmp/screen_a.png",
            }
            agent.task_planner = TaskPlanner(max_steps=8)
            agent.current_plan = {
                "planner_version": "phase2-v1",
                "task": "打开设置然后打开蓝牙",
                "steps": [
                    {
                        "step_name": "Step 1: 打开设置",
                        "instruction": "打开设置",
                        "success_criteria": "设置页面已打开",
                    },
                    {
                        "step_name": "Step 2: 打开蓝牙",
                        "instruction": "打开蓝牙",
                        "success_criteria": "蓝牙已开启",
                    },
                ],
            }
            agent.current_step_index = 1
            agent.step_status = {"0": "done", "1": "in_progress"}
            agent.current_checkpoint_path = agent._get_checkpoint_path(agent.context["task_request"])

            PhoneAgent._save_checkpoint(
                agent,
                last_action=agent.context["last_action"],
                last_screenshot=agent.context["last_screenshot"],
            )

            ck_path = Path(agent.current_checkpoint_path)
            self.assertTrue(ck_path.exists())

            payload = json.loads(ck_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["current_step_index"], 1)
            self.assertEqual(payload["step_status"], {"0": "done", "1": "in_progress"})
            self.assertEqual(payload["last_action"]["action"], "tap")
            self.assertEqual(payload["last_screenshot"], "/tmp/screen_a.png")
            self.assertIn("timestamp", payload)

            restored = PhoneAgent._load_checkpoint(agent, agent.context["task_request"])
            self.assertIsNotNone(restored)
            self.assertEqual(restored["current_step_index"], 1)
            self.assertEqual(restored["step_status"]["0"], "done")


if __name__ == "__main__":
    unittest.main(verbosity=2)
