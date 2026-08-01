import unittest

from meeter.local_models import DiarizationTurn
from meeter.pipeline import assign_diarization, recognize_clusters, select_voice_snippets


class PipelineTests(unittest.TestCase):
    def test_assigns_speaker_by_maximum_overlap(self):
        segments = [
            {"start": 0.0, "end": 2.0, "text": "Hello"},
            {"start": 2.2, "end": 5.0, "text": "Hi"},
        ]
        turns = [DiarizationTurn(0, 2.5, "A"), DiarizationTurn(2.5, 5.0, "B")]
        result = assign_diarization(segments, turns)
        self.assertEqual(result[0]["speaker"], "Unknown speaker 1")
        self.assertEqual(result[1]["speaker"], "Unknown speaker 2")

    def test_recognizes_match_and_preserves_unknown(self):
        rows = [
            {"cluster": "A", "speaker": "Unknown speaker 1"},
            {"cluster": "B", "speaker": "Unknown speaker 2"},
        ]
        embeddings = {"A": [1.0, 0.0], "B": [0.0, 1.0]}
        profiles = [{"id": "speaker_asha", "name": "Asha", "embedding": [0.99, 0.01]}]
        result, participants = recognize_clusters(rows, embeddings, profiles, threshold=0.8)
        self.assertEqual(result[0]["speaker"], "Asha")
        self.assertEqual(result[1]["speaker"], "Unknown speaker 1")
        self.assertTrue(participants[0]["known"])
        self.assertFalse(participants[1]["known"])

    def test_voice_snippets_choose_longest_centered_turn_per_identity(self):
        turns = [
            DiarizationTurn(0.0, 2.0, "A"),
            DiarizationTurn(3.0, 13.0, "B"),
            DiarizationTurn(14.0, 15.4, "C"),
            DiarizationTurn(20.0, 25.0, "D"),
        ]
        rows = [
            {"cluster": "A", "speaker_id": "speaker_asha"},
            {"cluster": "B", "speaker_id": "speaker_asha"},
            {"cluster": "C", "speaker_id": None},
            {"cluster": "D", "speaker_id": None},
        ]
        participants = [
            {"id": "speaker_asha", "name": "Asha", "known": True},
            {"id": "C", "name": "Unknown speaker 1", "known": False},
            {"id": "D", "name": "Unknown speaker 2", "known": False},
        ]

        result = select_voice_snippets(turns, rows, participants)

        self.assertEqual(result[0]["voice_snippet"], {"start_seconds": 5.0, "end_seconds": 11.0})
        self.assertNotIn("voice_snippet", result[1])
        self.assertEqual(result[2]["voice_snippet"], {"start_seconds": 20.0, "end_seconds": 25.0})
        self.assertNotIn("voice_snippet", participants[0])


if __name__ == "__main__":
    unittest.main()
