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


class Phase1FunctionTests(unittest.TestCase):
    def _new_agent(self):
        agent = object.__new__(PhoneAgent)
        agent.config = {
            "enable_dynamic_retry_budget": True,
            "retry_budget_simple": 2,
            "retry_budget_medium": 4,
            "retry_budget_complex": 6,
            "retry_budget_cap": 8,
            "max_retries": 3,
        }
        return agent

    def test_classify_retry_reason(self):
        agent = self._new_agent()
        self.assertEqual(
            agent._classify_retry_reason("Tap action missing coordinates", {"action": "tap"}),
            "坐标偏差",
        )
        self.assertEqual(
            agent._classify_retry_reason("ADB command timed out after 15s", {"action": "wait"}),
            "页面未加载",
        )
        self.assertEqual(
            agent._classify_retry_reason("Permission dialog blocked interaction", {"action": "tap"}),
            "弹窗阻断",
        )
        self.assertEqual(agent._classify_retry_reason("unexpected error"), "未知")

    def test_dynamic_retry_budget(self):
        agent = self._new_agent()

        level_s, budget_s = agent._resolve_retry_budget("打开相机")
        self.assertEqual(level_s, "simple")
        self.assertEqual(budget_s, 2)

        level_m, budget_m = agent._resolve_retry_budget("打开设置然后搜索蓝牙并且返回首页")
        self.assertEqual(level_m, "medium")
        self.assertEqual(budget_m, 4)

        level_c, budget_c = agent._resolve_retry_budget("打开抖音并持续刷视频 for a while then keep scrolling until stopped")
        self.assertEqual(level_c, "complex")
        self.assertEqual(budget_c, 6)

        agent.config["retry_budget_complex"] = 7
        agent.config["retry_budget_cap"] = 5
        _, capped = agent._resolve_retry_budget("打开抖音并持续刷视频，然后继续循环并且 until done")
        self.assertEqual(capped, 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
