import sys
import tempfile
import types
import unittest


# Stub heavy dependency chain before importing ui
fake_qwen = types.ModuleType("qwen_vl_agent")


class _DummyQwenVLAgent:
    pass


fake_qwen.QwenVLAgent = _DummyQwenVLAgent
sys.modules["qwen_vl_agent"] = fake_qwen

import ui  # noqa: E402


class P1UISmokeTest(unittest.TestCase):
    def _patch_adb_ok(self):
        original_run = ui.subprocess.run

        class R:
            def __init__(self, stdout="", stderr=""):
                self.stdout = stdout
                self.stderr = stderr

        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["adb", "version"]:
                return R(stdout="Android Debug Bridge version 1.0.41")
            if cmd[:2] == ["adb", "devices"]:
                return R(stdout="List of devices attached\nserial123\tdevice\n")
            if cmd[:5] == ["adb", "-s", "serial123", "shell", "wm"]:
                return R(stdout="Physical size: 1080x2400\n")
            raise AssertionError(f"unexpected command: {cmd}")

        ui.subprocess.run = fake_run
        return original_run

    def test_ui_contains_task_tree_panel(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = ui.get_default_config()
            cfg["runtime_config_path"] = f"{tmp}/config.json"
            cfg["screen_width"] = 720
            cfg["screen_height"] = 1280
            ui.current_config = cfg

            original_run = self._patch_adb_ok()
            try:
                demo = ui.create_ui()
                config = demo.config
            finally:
                ui.subprocess.run = original_run

            has_task_tree = any(
                comp.get("type") == "markdown"
                and comp.get("props", {}).get("elem_id") == "task-tree-panel"
                for comp in config.get("components", [])
                if isinstance(comp, dict)
            )
            self.assertTrue(has_task_tree, "任务树面板不存在")

            has_preset_dropdown = any(
                comp.get("type") == "dropdown"
                and comp.get("props", {}).get("label") == "预设任务"
                for comp in config.get("components", [])
                if isinstance(comp, dict)
            )
            self.assertTrue(has_preset_dropdown, "预设任务下拉不存在")

            if hasattr(demo, "close"):
                demo.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
