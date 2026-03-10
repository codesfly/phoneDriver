import unittest
from unittest import mock

from ios_bridge import IOSBridge, IOSBridgeError
from ios_service import IOSBridgeService, IOSServiceError


class IOSBridgeMinimalTests(unittest.TestCase):
    def test_disabled_bridge_must_fail_explicitly(self):
        bridge = IOSBridge({"ios_enabled": False})
        with self.assertRaises(IOSBridgeError):
            bridge.discover_devices()

    def test_non_macos_must_fail_explicitly(self):
        bridge = IOSBridge({"ios_enabled": True})
        with mock.patch("ios_bridge.platform.system", return_value="Linux"):
            with self.assertRaises(IOSBridgeError):
                bridge.health_check()

    def test_wda_not_ready_with_autostart_disabled_must_fail(self):
        bridge = IOSBridge(
            {
                "ios_enabled": True,
                "ios_default_udid": "udid-1",
                "ios_auto_start_tunnel": False,
                "ios_auto_start_runwda": False,
            }
        )

        with mock.patch("ios_bridge.platform.system", return_value="Darwin"), \
            mock.patch("ios_bridge.shutil.which", return_value="/usr/local/bin/go-ios"), \
            mock.patch.object(bridge, "_is_wda_ready", return_value=False):
            with self.assertRaises(IOSBridgeError) as ctx:
                bridge.ensure_wda_ready()
            self.assertIn("auto-start disabled", str(ctx.exception))

    def test_prepare_auto_starts_and_creates_session(self):
        bridge = IOSBridge(
            {
                "ios_enabled": True,
                "ios_default_udid": "udid-1",
                "ios_auto_start_tunnel": True,
                "ios_auto_start_runwda": True,
            }
        )

        with mock.patch("ios_bridge.platform.system", return_value="Darwin"), \
            mock.patch("ios_bridge.shutil.which", return_value="/usr/local/bin/go-ios"), \
            mock.patch.object(bridge, "_is_wda_ready", return_value=False), \
            mock.patch.object(bridge, "start_tunnel", return_value={"ok": True, "started": True}), \
            mock.patch.object(bridge, "start_runwda", return_value={"ok": True, "started": True}), \
            mock.patch.object(bridge, "_wait_wda_ready", return_value=None), \
            mock.patch.object(
                bridge,
                "_wda_post",
                return_value={"value": {"sessionId": "session-123", "capabilities": {}}},
            ):
            result = bridge.prepare(ensure_session=True)
            self.assertTrue(result["ok"])
            self.assertEqual(result["session"]["session_id"], "session-123")

    def test_service_missing_fields_must_fail(self):
        service = IOSBridgeService(mock.Mock())
        with self.assertRaises(IOSServiceError):
            service.call("tap", {"x": 1})


if __name__ == "__main__":
    unittest.main(verbosity=2)
