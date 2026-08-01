import tempfile
import unittest
from pathlib import Path

from meeter.demo import demo_meeting
from meeter.storage import LocalStore


class StorageTests(unittest.TestCase):
    def test_round_trip_and_list(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(Path(directory))
            meeting = demo_meeting().to_dict()
            store.save_meeting(meeting)
            loaded = store.get_meeting(meeting["id"])
            self.assertEqual(loaded["title"], meeting["title"])
            self.assertEqual(store.list_meetings()[0]["action_count"], 4)

    def test_rejects_path_like_meeting_id(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(Path(directory))
            meeting = demo_meeting().to_dict()
            meeting["id"] = "meeting_../../escape"
            with self.assertRaises(ValueError):
                store.save_meeting(meeting)

    def test_audio_attachment_is_promoted_to_validated_meeting_path(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(Path(directory))
            upload = store.save_upload("recording.webm", b"0123456789")

            metadata = store.save_meeting_audio("meeting_audio123", upload, "audio/webm")

            self.assertEqual(metadata, {"mime_type": "audio/webm", "size_bytes": 10})
            self.assertFalse(upload.exists())
            self.assertEqual(store.get_meeting_audio("meeting_audio123").read_bytes(), b"0123456789")
            self.assertIsNone(store.meeting_audio_path("meeting_../../escape"))

    def test_rename_meeting_validates_title_and_preserves_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(Path(directory))
            meeting = demo_meeting().to_dict()
            store.save_meeting(meeting)

            renamed = store.rename_meeting(meeting["id"], "  Product sync 🚀  ")

            self.assertEqual(renamed["title"], "Product sync 🚀")
            self.assertEqual(renamed["transcript"], meeting["transcript"])
            for invalid in ["", "   ", "bad\nname", "bad\x00name", "x" * 121, 42]:
                with self.assertRaises(ValueError):
                    store.rename_meeting(meeting["id"], invalid)

    def test_delete_meeting_removes_owned_audio_only(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(Path(directory))
            first = demo_meeting().to_dict()
            second = demo_meeting().to_dict()
            second["id"] = "meeting_other123"
            store.save_meeting(first)
            store.save_meeting(second)
            store.save_speakers([{"id": "speaker_1", "name": "A"}])
            upload = store.save_upload("clip.webm", b"audio")
            store.save_meeting_audio(first["id"], upload, "audio/webm")

            self.assertTrue(store.delete_meeting(first["id"]))
            self.assertIsNone(store.get_meeting(first["id"]))
            self.assertIsNone(store.get_meeting_audio(first["id"]))
            self.assertIsNotNone(store.get_meeting(second["id"]))
            self.assertEqual(store.load_speakers()[0]["name"], "A")
            self.assertFalse(store.delete_meeting(first["id"]))

    def test_settings_default_and_atomic_section_update(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(Path(directory))
            self.assertTrue(store.load_settings()["audio"]["retain_recordings"])

            store.update_settings_section("audio", {"retain_recordings": False})
            store.update_settings_section("future_feature", {"enabled": True})

            settings = store.load_settings()
            self.assertFalse(settings["audio"]["retain_recordings"])
            self.assertTrue(settings["future_feature"]["enabled"])

    def test_invalid_settings_fail_closed_for_audio_retention(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(Path(directory))
            store.settings_path.write_text("not-json", encoding="utf-8")

            settings, error = store.load_settings_state()

            self.assertFalse(settings["audio"]["retain_recordings"])
            self.assertIn("disabled", error)


if __name__ == "__main__":
    unittest.main()
