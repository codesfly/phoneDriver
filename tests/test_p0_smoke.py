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


class P0SmokeTest(unittest.TestCase):
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

    def test_ui_health_check_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = ui.get_default_config()
            cfg["runtime_config_path"] = f"{tmp}/config.json"
            cfg["screen_width"] = 720
            cfg["screen_height"] = 1280

            original_run = self._patch_adb_ok()
            try:
                result = ui.run_health_check(cfg, persist_runtime_config=False)
            finally:
                ui.subprocess.run = original_run

            self.assertEqual(result.get("status"), "ok")
            self.assertEqual(result.get("device_id"), "serial123")
            self.assertEqual(result.get("screen_width"), 1080)
            self.assertEqual(result.get("screen_height"), 2400)
            status_text = ui.format_health_result(result)
            self.assertIn("ADB 正常", status_text)
            self.assertIn("serial123", status_text)

    def test_ui_create_smoke_with_healthcheck(self):
        original_run = self._patch_adb_ok()
        original_save = ui.save_config

        def fake_save_config(config, config_path="config.json"):
            return True

        ui.save_config = fake_save_config
        try:
            ui.current_config = ui.get_default_config()
            demo = ui.create_ui()
        finally:
            ui.subprocess.run = original_run
            ui.save_config = original_save

        self.assertIsNotNone(demo)
        if hasattr(demo, "close"):
            demo.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
