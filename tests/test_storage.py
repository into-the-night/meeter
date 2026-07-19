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


if __name__ == "__main__":
    unittest.main()

