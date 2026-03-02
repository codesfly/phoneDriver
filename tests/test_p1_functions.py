import sys
import types
import unittest


# Stub heavy dependency chain before importing ui
fake_qwen = types.ModuleType("qwen_vl_agent")


class _DummyQwenVLAgent:
    pass


fake_qwen.QwenVLAgent = _DummyQwenVLAgent
sys.modules["qwen_vl_agent"] = fake_qwen

import ui  # noqa: E402


class P1FunctionTests(unittest.TestCase):
    def test_task_tree_markdown_format(self):
        plan = {
            "planner_version": "phase2-v1",
            "task": "打开设置检查网络",
            "steps": [
                {
                    "step_name": "Step 1: 打开设置",
                    "instruction": "打开设置应用",
                    "success_criteria": "看到设置首页",
                },
                {
                    "step_name": "Step 2: 检查网络",
                    "instruction": "进入网络页面并检查 Wi‑Fi",
                    "success_criteria": "网络状态可见",
                },
            ],
        }
        step_status = {"0": "done", "1": "in_progress"}

        md = ui.format_task_tree_markdown(plan, step_status, current_step_index=1)

        self.assertIn("任务树 / 规划步骤", md)
        self.assertIn("当前步骤索引：2", md)
        self.assertIn("Step 1: 打开设置", md)
        self.assertIn("instruction: 打开设置应用", md)
        self.assertIn("success_criteria: 看到设置首页", md)
        self.assertIn("[已完成]", md)
        self.assertIn("[执行中]", md)

    def test_apply_preset_task_fill(self):
        text, status = ui.apply_preset_task("打开浏览器搜索天气", "")
        self.assertIn("打开浏览器", text)
        self.assertIn("搜索上海天气", text)
        self.assertIn("已应用预设任务", status)


if __name__ == "__main__":
    unittest.main(verbosity=2)
