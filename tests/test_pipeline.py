import unittest
from unittest.mock import patch

from meeter.local_models import DiarizationTurn, QwenASRAdapter
from meeter.pipeline import _deduplicate_chunk_rows, aggregate_languages, assign_diarization, cluster_voice_embeddings, recognize_clusters, reconcile_chunk_clusters, select_voice_snippets


class PipelineTests(unittest.TestCase):
    def test_language_aggregation_ignores_one_small_noisy_chunk(self):
        self.assertEqual(aggregate_languages([("en", 120), ("hi", 3)]), "en")
        self.assertEqual(aggregate_languages([("en", 80), ("hi", 30)]), "hi-en")

    def test_voice_clustering_is_independent_of_embedding_order(self):
        first, _ = cluster_voice_embeddings({"B": [0.99, 0.01], "C": [0.0, 1.0], "A": [1.0, 0.0]}, 0.8)
        second, _ = cluster_voice_embeddings({"A": [1.0, 0.0], "B": [0.99, 0.01], "C": [0.0, 1.0]}, 0.8)
        self.assertEqual(first["A"], first["B"])
        self.assertNotEqual(first["A"], first["C"])
        self.assertEqual({key: first[key] == first["A"] for key in first}, {key: second[key] == second["A"] for key in second})

    def test_stream_overlap_is_owned_by_only_one_batch(self):
        chunks = [
            {"index": 0, "start": 0.0, "end": 31.5, "rows": [
                {"start": 28.0, "end": 30.0, "text": "before", "cluster": "A"},
                {"start": 30.5, "end": 31.2, "text": "duplicate old", "cluster": "A"},
            ]},
            {"index": 1, "start": 30.0, "end": 61.5, "rows": [
                {"start": 30.4, "end": 31.0, "text": "duplicate new", "cluster": "B"},
                {"start": 31.2, "end": 33.0, "text": "after", "cluster": "B"},
            ]},
        ]

        rows = _deduplicate_chunk_rows(chunks)

        self.assertEqual([row["text"] for row in rows], ["before", "after"])

    def test_stream_speaker_embeddings_reconcile_across_batches(self):
        chunks = [
            {"index": 0, "start": 0.0, "end": 31.5, "embeddings": {"chunk_0:A": [1.0, 0.0]}, "rows": [
                {"start": 2.0, "end": 4.0, "text": "One", "cluster": "chunk_0:A"},
            ]},
            {"index": 1, "start": 30.0, "end": 61.5, "embeddings": {"chunk_1:B": [0.99, 0.01]}, "rows": [
                {"start": 35.0, "end": 37.0, "text": "Two", "cluster": "chunk_1:B"},
            ]},
        ]

        rows, embeddings = reconcile_chunk_clusters(chunks, [], threshold=0.8)

        self.assertEqual(rows[0]["cluster"], rows[1]["cluster"])
        self.assertEqual(len(embeddings), 1)

    def test_qwen_chunks_merge_adjacent_same_speaker_turns(self):
        turns = [
            DiarizationTurn(0.0, 2.0, "A"),
            DiarizationTurn(2.2, 4.0, "A"),
            DiarizationTurn(4.1, 6.0, "B"),
        ]
        result = QwenASRAdapter._merge_turns(turns)
        self.assertEqual([(turn.start, turn.end, turn.label) for turn in result], [(0.0, 4.0, "A"), (4.1, 6.0, "B")])

    def test_qwen_uses_conservative_mps_batch_by_default(self):
        self.assertEqual(QwenASRAdapter._batch_size("mps"), 2)
        self.assertEqual(QwenASRAdapter._batch_size("cuda:0"), 8)
        self.assertEqual(QwenASRAdapter._batch_size("cpu"), 1)

    def test_qwen_batch_size_can_be_tuned_after_measurement(self):
        with patch.dict("os.environ", {"MEETER_ASR_BATCH_SIZE": "4"}):
            self.assertEqual(QwenASRAdapter._batch_size("mps"), 4)

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

    def test_reconciles_unmatched_clusters_with_similar_voice_embeddings(self):
        rows = [{"cluster": "A"}, {"cluster": "B"}]
        embeddings = {"A": [1.0, 0.0], "B": [0.99, 0.01]}
        result, participants = recognize_clusters(rows, embeddings, [], threshold=0.8)
        self.assertEqual(result[0]["speaker"], result[1]["speaker"])
        self.assertEqual(len(participants), 1)

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
