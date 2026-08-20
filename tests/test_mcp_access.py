import tempfile
import unittest
from pathlib import Path

from meeter.demo import demo_meeting
from meeter.mcp_access import (
    McpPrivacyConfig,
    MeetingContextService,
    PrivacyLevel,
    ReadOnlyMeetingStore,
)
from meeter.storage import LocalStore


class McpAccessTests(unittest.TestCase):
    def service(
        self,
        directory: str,
        level: PrivacyLevel = PrivacyLevel.INSIGHTS,
        allowed: frozenset[str] | None = None,
    ) -> MeetingContextService:
        return MeetingContextService(
            ReadOnlyMeetingStore(Path(directory)),
            McpPrivacyConfig(level=level, allowed_meeting_ids=allowed),
        )

    def test_read_only_store_does_not_create_data_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "does-not-exist"
            store = ReadOnlyMeetingStore(missing)
            self.assertEqual(store.iter_meetings(), [])
            self.assertFalse(missing.exists())

    def test_insights_mode_never_returns_transcript(self):
        with tempfile.TemporaryDirectory() as directory:
            meeting = demo_meeting().to_dict()
            LocalStore(Path(directory)).save_meeting(meeting)
            service = self.service(directory)

            result = service.get_meeting_insights(meeting["id"])

            self.assertNotIn("transcript", result["meeting"])
            self.assertEqual(result["privacy"]["transcript_access"], "none")
            with self.assertRaises(PermissionError):
                service.get_transcript_excerpts(meeting["id"], "beta")

    def test_excerpt_search_returns_only_matching_turns(self):
        with tempfile.TemporaryDirectory() as directory:
            meeting = demo_meeting().to_dict()
            LocalStore(Path(directory)).save_meeting(meeting)
            service = self.service(directory, PrivacyLevel.EXCERPTS)

            result = service.get_transcript_excerpts(meeting["id"], "Finance")

            self.assertEqual(len(result["excerpts"]), 2)
            self.assertTrue(all("finance" in item["text"].lower() for item in result["excerpts"]))
            self.assertNotIn("speaker_id", result["excerpts"][0])

    def test_full_mode_returns_text_transcript_but_no_audio_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            meeting = demo_meeting().to_dict()
            meeting["audio"] = {"mime_type": "audio/webm", "size_bytes": 1234}
            meeting["participants"][0]["voice_snippet"] = {"start_seconds": 1.5, "end_seconds": 5.0}
            LocalStore(Path(directory)).save_meeting(meeting)
            service = self.service(directory, PrivacyLevel.FULL)

            result = service.get_meeting_transcript(meeting["id"])

            self.assertEqual(len(result["transcript"]), len(meeting["transcript"]))
            self.assertNotIn("source_name", result)
            self.assertNotIn("audio", result)
            self.assertNotIn("confidence", result["transcript"][0])
            insights = service.get_meeting_insights(meeting["id"])
            self.assertNotIn("voice_snippet", insights["meeting"]["participants"][0])

    def test_full_mode_can_query_a_single_speaker_transcript(self):
        with tempfile.TemporaryDirectory() as directory:
            meeting = demo_meeting().to_dict()
            LocalStore(Path(directory)).save_meeting(meeting)
            service = self.service(directory, PrivacyLevel.FULL)

            result = service.get_speaker_transcript(meeting["id"], "Maya Chen")

            self.assertEqual(result["speaker"], "Maya Chen")
            self.assertGreater(len(result["transcript"]), 0)
            self.assertTrue(all(turn["speaker"] == "Maya Chen" for turn in result["transcript"]))
            with self.assertRaises(PermissionError):
                self.service(directory).get_speaker_transcript(meeting["id"], "Maya Chen")

    def test_allowlist_hides_other_meetings(self):
        with tempfile.TemporaryDirectory() as directory:
            first = demo_meeting().to_dict()
            second = demo_meeting().to_dict()
            second["id"] = "meeting_second"
            second["title"] = "Private meeting"
            store = LocalStore(Path(directory))
            store.save_meeting(first)
            store.save_meeting(second)
            service = self.service(directory, allowed=frozenset({first["id"]}))

            listing = service.list_meetings()

            self.assertEqual([item["id"] for item in listing["meetings"]], [first["id"]])
            with self.assertRaises(LookupError):
                service.get_meeting_insights(second["id"])

    def test_meeting_rename_and_delete_are_immediately_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            meeting = demo_meeting().to_dict()
            store = LocalStore(Path(directory))
            store.save_meeting(meeting)
            service = self.service(directory)

            store.rename_meeting(meeting["id"], "Renamed planning")
            self.assertEqual(
                service.get_meeting_insights(meeting["id"])["meeting"]["title"],
                "Renamed planning",
            )

            store.delete_meeting(meeting["id"])
            self.assertEqual(service.list_meetings()["meetings"], [])
            with self.assertRaises(LookupError):
                service.get_meeting_insights(meeting["id"])

    def test_pii_is_redacted_from_insights_and_transcript(self):
        with tempfile.TemporaryDirectory() as directory:
            meeting = demo_meeting().to_dict()
            meeting["summary"] = "Email owner@example.com or call +91 98765 43210."
            meeting["transcript"][0]["text"] = "Reach me at owner@example.com"
            meeting["actions"][0]["internal_secret"] = "must-not-leak"
            LocalStore(Path(directory)).save_meeting(meeting)
            service = self.service(directory, PrivacyLevel.FULL)

            insights = service.get_meeting_insights(meeting["id"])
            transcript = service.get_meeting_transcript(meeting["id"])

            self.assertIn("[redacted email]", insights["meeting"]["summary"])
            self.assertIn("[redacted phone]", insights["meeting"]["summary"])
            self.assertIn("[redacted email]", transcript["transcript"][0]["text"])
            self.assertEqual(insights["meeting"]["actions"][0]["due"], "2026-07-21")
            self.assertNotIn("internal_secret", insights["meeting"]["actions"][0])

    def test_date_only_filters_include_the_whole_day(self):
        with tempfile.TemporaryDirectory() as directory:
            meeting = demo_meeting().to_dict()
            meeting["created_at"] = "2026-07-20T18:30:00+00:00"
            LocalStore(Path(directory)).save_meeting(meeting)
            service = self.service(directory)

            result = service.list_meetings(date_from="2026-07-20", date_to="2026-07-20")

            self.assertEqual(result["total_matches"], 1)

    def test_search_and_actions_use_only_permitted_data(self):
        with tempfile.TemporaryDirectory() as directory:
            meeting = demo_meeting().to_dict()
            LocalStore(Path(directory)).save_meeting(meeting)
            service = self.service(directory)

            search = service.search_meetings("warehouse")
            actions = service.get_action_items(owner="Leena")

            self.assertEqual(search["matches"][0]["meeting_id"], meeting["id"])
            self.assertNotIn("transcript_excerpts", search["matches"][0])
            self.assertEqual(len(actions["actions"]), 2)


if __name__ == "__main__":
    unittest.main()
