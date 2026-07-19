import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from meeter.storage import LocalStore
from server import MeeterServer


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = MeeterServer(("127.0.0.1", 0), LocalStore(Path(self.temp.name)))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def request_json(self, path, data=None):
        body = data.encode() if data is not None else None
        request = urllib.request.Request(self.base + path, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.load(response)

    def test_health_and_demo_contract(self):
        status, health = self.request_json("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["readiness"]["network"], "loopback-only")

        status, meeting = self.request_json("/api/meetings/demo", "{}")
        self.assertEqual(status, 201)
        self.assertTrue(meeting["actions"])
        status, index = self.request_json("/api/meetings")
        self.assertEqual(index["meetings"][0]["id"], meeting["id"])

    def test_static_response_has_security_policy(self):
        with urllib.request.urlopen(self.base + "/", timeout=3) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")


if __name__ == "__main__":
    unittest.main()
