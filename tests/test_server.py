import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from pathlib import Path

from meeter.demo import demo_meeting
from meeter.storage import LocalStore
from server import JobManager, MeeterServer


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

    def request_json(self, path, data=None, method=None):
        body = data.encode() if data is not None else None
        request = urllib.request.Request(self.base + path, data=body, headers={"Content-Type": "application/json"}, method=method)
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
            self.assertIn("display-capture=(self)", response.headers["Permissions-Policy"])
            self.assertIn("microphone=(self)", response.headers["Permissions-Policy"])

    def test_recording_ui_offers_mic_and_device_audio(self):
        with urllib.request.urlopen(self.base + "/", timeout=3) as response:
            page = response.read().decode("utf-8")
            self.assertIn('data-source="mic"', page)
            self.assertIn('data-source="device"', page)
            self.assertIn('id="microphone-select"', page)
            self.assertIn('data-action="pause-recording"', page)
            self.assertIn('data-action="cancel-recording"', page)
            self.assertIn('id="cancel-confirm-modal"', page)
            self.assertIn('data-action="confirm-cancel"', page)
            self.assertNotIn("Audio check starts when you record", page)
            self.assertNotIn("Ready to record", page)

    def test_audio_settings_default_update_and_managed_override(self):
        status, settings = self.request_json("/api/settings/audio")
        self.assertEqual(status, 200)
        self.assertTrue(settings["retain_recordings"])
        self.assertFalse(settings["managed"])

        status, settings = self.request_json(
            "/api/settings/audio", '{"retain_recordings": false}', method="PUT"
        )
        self.assertEqual(status, 200)
        self.assertFalse(settings["retain_recordings"])

        with patch.dict("os.environ", {"MEETER_KEEP_AUDIO": "1"}):
            status, settings = self.request_json("/api/settings/audio")
            self.assertTrue(settings["retain_recordings"])
            self.assertTrue(settings["managed"])
            request = urllib.request.Request(
                self.base + "/api/settings/audio",
                data=b'{"retain_recordings": false}',
                headers={"Content-Type": "application/json"},
                method="PUT",
            )
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(request, timeout=3)
            self.assertEqual(raised.exception.code, 409)

    def test_meeting_audio_full_head_ranges_and_missing(self):
        meeting = demo_meeting().to_dict()
        upload = self.server.store.save_upload("clip.webm", b"0123456789")
        meeting["audio"] = self.server.store.save_meeting_audio(meeting["id"], upload, "audio/webm")
        self.server.store.save_meeting(meeting)
        url = self.base + f"/api/meetings/{meeting['id']}/audio"

        with urllib.request.urlopen(url, timeout=3) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"0123456789")
            self.assertEqual(response.headers["Accept-Ranges"], "bytes")
            self.assertEqual(response.headers["Cache-Control"], "private, no-store")

        head = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(head, timeout=3) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Content-Length"], "10")
            self.assertEqual(response.read(), b"")

        for header, expected, content_range in [
            ("bytes=2-5", b"2345", "bytes 2-5/10"),
            ("bytes=7-", b"789", "bytes 7-9/10"),
            ("bytes=-3", b"789", "bytes 7-9/10"),
        ]:
            request = urllib.request.Request(url, headers={"Range": header})
            with urllib.request.urlopen(request, timeout=3) as response:
                self.assertEqual(response.status, 206)
                self.assertEqual(response.read(), expected)
                self.assertEqual(response.headers["Content-Range"], content_range)

        invalid = urllib.request.Request(url, headers={"Range": "bytes=99-100"})
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(invalid, timeout=3)
        self.assertEqual(raised.exception.code, 416)
        self.assertEqual(raised.exception.headers["Content-Range"], "bytes */10")

        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(self.base + "/api/meetings/meeting_missing/audio", timeout=3)
        self.assertEqual(raised.exception.code, 404)

    def test_transcript_playback_ui_contract(self):
        with urllib.request.urlopen(self.base + "/app.js", timeout=3) as response:
            script = response.read().decode("utf-8")
        self.assertIn('id="meeting-audio"', script)
        self.assertIn('data-action="seek-transcript"', script)
        self.assertIn("active-audio", script)
        self.assertIn("Audio unavailable", script)

    def test_voice_snippet_ui_contract(self):
        with urllib.request.urlopen(self.base + "/app.js", timeout=3) as response:
            script = response.read().decode("utf-8")
        self.assertIn('data-action="play-voice-snippet"', script)
        self.assertIn("toggleVoiceSnippet", script)
        self.assertIn("meetingPlayback.snippetEnd", script)

    def test_speaker_rename_preserves_voice_snippet(self):
        meeting = demo_meeting().to_dict()
        participant = meeting["participants"][0]
        old_name = participant["name"]
        participant["voice_snippet"] = {"start_seconds": 2.0, "end_seconds": 6.0}
        self.server.store.save_meeting(meeting)

        status, updated = self.request_json(
            f"/api/meetings/{meeting['id']}/speaker-name",
            json.dumps({"old_name": old_name, "new_name": "Renamed speaker"}),
        )

        self.assertEqual(status, 200)
        renamed = next(item for item in updated["participants"] if item["name"] == "Renamed speaker")
        self.assertEqual(renamed["voice_snippet"], {"start_seconds": 2.0, "end_seconds": 6.0})


class JobManagerTests(unittest.TestCase):
    def test_success_publishes_meeting_only_after_audio_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(Path(directory))

            class Pipeline:
                def __init__(self):
                    self.store = store

                def process(self, audio_path, source_name, progress):
                    meeting = demo_meeting().to_dict()
                    self.assert_not_published = store.get_meeting(meeting["id"]) is None
                    return meeting

            pipeline = Pipeline()
            manager = JobManager(pipeline)
            upload = store.save_upload("meeting.webm", b"audio-data")
            manager._jobs["job_test"] = {"id": "job_test"}
            manager._run("job_test", upload, "meeting.webm", "audio/webm", True)

            job = manager.get("job_test")
            saved = store.get_meeting(job["meeting_id"])
            self.assertTrue(pipeline.assert_not_published)
            self.assertEqual(job["state"], "complete")
            self.assertEqual(saved["audio"]["size_bytes"], 10)
            self.assertEqual(store.get_meeting_audio(saved["id"]).read_bytes(), b"audio-data")

    def test_processing_failure_cleans_temporary_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(Path(directory))

            class Pipeline:
                def __init__(self):
                    self.store = store

                def process(self, audio_path, source_name, progress):
                    raise RuntimeError("broken")

            manager = JobManager(Pipeline())
            upload = store.save_upload("meeting.webm", b"audio-data")
            manager._jobs["job_test"] = {"id": "job_test"}
            manager._run("job_test", upload, "meeting.webm", "audio/webm", True)

            self.assertEqual(manager.get("job_test")["state"], "error")
            self.assertFalse(upload.exists())


if __name__ == "__main__":
    unittest.main()
