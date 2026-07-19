import unittest

from meeter.local_models import DiarizationTurn
from meeter.pipeline import assign_diarization, recognize_clusters


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


if __name__ == "__main__":
    unittest.main()

