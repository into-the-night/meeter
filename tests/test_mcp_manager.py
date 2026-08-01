import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from meeter.mcp_manager import McpServiceManager


class McpServiceManagerTests(unittest.TestCase):
    def test_start_stop_and_restart_own_child(self):
        process = MagicMock()
        process.poll.return_value = None
        process.wait.return_value = 0
        config = {"enabled": True, "privacy": "insights", "redact_pii": True}
        manager = McpServiceManager(Path("/tmp/meeter-test"), lambda: config, 54321)
        manager._desired = True
        with patch("meeter.mcp_manager.subprocess.Popen", return_value=process) as popen, patch("meeter.mcp_manager.threading.Thread.start"):
            manager._start()
            self.assertEqual(manager.status()["state"], "running")
            command = popen.call_args.args[0]
            self.assertIn("streamable-http", command)
            self.assertIn("--parent-pid", command)
            manager.stop()
            process.terminate.assert_called_once()

    def test_disabled_does_not_spawn(self):
        manager = McpServiceManager(Path("/tmp/meeter-test"), lambda: {"enabled": False})
        manager._desired = True
        with patch("meeter.mcp_manager.subprocess.Popen") as popen:
            manager._start()
        popen.assert_not_called()
        self.assertEqual(manager.status()["state"], "stopped")

    def test_spawn_failure_is_reported(self):
        manager = McpServiceManager(Path("/tmp/meeter-test"), lambda: {"enabled": True, "privacy": "insights", "redact_pii": True})
        manager._desired = True
        with patch("meeter.mcp_manager.subprocess.Popen", side_effect=OSError("missing runtime")):
            manager._start()
        self.assertEqual(manager.status()["state"], "error")
        self.assertIn("missing runtime", manager.status()["error"])


if __name__ == "__main__":
    unittest.main()
