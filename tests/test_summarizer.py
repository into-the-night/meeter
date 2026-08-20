import json
import unittest
from unittest.mock import patch

from meeter.summarizer import fallback_summary, ground_actions, parse_model_json, summarize, transcript_batches


class SummarizerTests(unittest.TestCase):
    def test_model_json_is_normalized(self):
        raw = """```json
        {"title":"Launch sync","summary":"We aligned.","decisions":["Ship Friday"],
        "actions":[{"text":"Send the plan","owner":"Asha","due":"2026-07-20","priority":"URGENT","evidence_quote":"I will send the plan","evidence_start":4}],
        "discussion":[],"risks":[]}
        ```"""
        result = parse_model_json(raw)
        self.assertEqual(result["title"], "Launch sync")
        self.assertEqual(result["actions"][0]["priority"], "medium")

    def test_fallback_only_extracts_commitment_like_sentences(self):
        transcript = [
            {"speaker": "Asha", "start": 0, "text": "The dashboard looks clear."},
            {"speaker": "Ben", "start": 4, "text": "I will send the revised plan. We agreed to ship Friday."},
        ]
        result = fallback_summary(transcript)
        self.assertEqual(len(result["actions"]), 1)
        self.assertEqual(result["actions"][0]["owner"], "Ben")
        self.assertTrue(result["decisions"])

    def test_invalid_model_output_uses_local_fallback(self):
        result, note = summarize([{"speaker": "A", "start": 0, "text": "I will follow up."}], lambda _: "not json")
        self.assertIn("fallback", note.lower())
        self.assertEqual(len(result["actions"]), 1)

    def test_incomplete_model_output_is_retried(self):
        responses = iter(["{}", '{"title":"Retry worked","summary":"Complete.","decisions":[],"actions":[],"discussion":[],"risks":[]}'])
        result, note = summarize([{"speaker": "A", "start": 0, "text": "Status update."}], lambda _: next(responses))
        self.assertEqual(note, "Local GGUF model")
        self.assertEqual(result["title"], "Retry worked")

    def test_transcript_batches_preserve_turn_order(self):
        transcript = [
            {"speaker": "A", "start": 0, "text": "A" * 40},
            {"speaker": "B", "start": 10, "text": "B" * 40},
            {"speaker": "A", "start": 20, "text": "C" * 40},
        ]
        batches = transcript_batches(transcript, maximum_characters=90, maximum_seconds=300)
        self.assertEqual([[turn["text"][0] for turn in batch] for batch in batches], [["A"], ["B"], ["C"]])

    def test_batched_summaries_capture_actions_then_reconcile(self):
        transcript = [
            {"speaker": "Asha", "start": 0, "text": "I will send the launch plan."},
            {"speaker": "Ben", "start": 400, "text": "I will confirm the release date."},
        ]
        partial_responses = iter([
            '{"title":"Part 1","summary":"Plan discussed.","decisions":[],"actions":[{"text":"Send launch plan","owner":"Asha","due":null,"priority":"medium","context":"Committed","evidence_quote":"I will send the launch plan.","evidence_start":0}],"discussion":[],"risks":[]}',
            '{"title":"Part 2","summary":"Date discussed.","decisions":[],"actions":[{"text":"Confirm release date","owner":"Ben","due":null,"priority":"medium","context":"Committed","evidence_quote":"I will confirm the release date.","evidence_start":400}],"discussion":[],"risks":[]}',
        ])
        final_response = '{"title":"Launch sync","summary":"Owners will close launch planning.","decisions":[],"actions":[{"text":"Send launch plan","owner":"Asha","due":null,"priority":"medium","context":"Committed","evidence_quote":"I will send the launch plan.","evidence_start":0},{"text":"Confirm release date","owner":"Ben","due":null,"priority":"medium","context":"Committed","evidence_quote":"I will confirm the release date.","evidence_start":400}],"discussion":[],"risks":[]}'

        result, note = summarize(transcript, lambda _: final_response, lambda _: next(partial_responses))

        self.assertEqual(len(result["actions"]), 2)
        self.assertIn("2 batches", note)

    def test_long_transcript_uses_extractive_batches_and_one_reconciliation_call(self):
        transcript = [
            {"speaker": "Asha", "start": 0, "text": "I will send the plan."},
            {"speaker": "Ben", "start": 400, "text": "The dependency is blocked."},
        ]
        response = '{"title":"Sync","summary":"Plan and blocker reviewed.","decisions":[],"actions":[{"text":"Send the plan","owner":"Asha","due":null,"priority":"medium","context":"Committed","evidence_quote":"I will send the plan.","evidence_start":0}],"discussion":[],"risks":["Dependency is blocked"]}'
        prompts = []
        with patch.dict("os.environ", {
            "MEETER_EXTRACTIVE_RECONCILIATION": "1",
            "MEETER_SUMMARY_RECONCILE_MIN_CHARS": "1",
        }):
            result, note = summarize(transcript, lambda prompt: prompts.append(prompt) or response)

        self.assertEqual(len(prompts), 1)
        self.assertEqual(len(result["actions"]), 1)
        self.assertIn("extractive batches", note)

    def test_fallback_recognizes_hindi_commitment_and_owner(self):
        result = fallback_summary([{"speaker": "Asha", "start": 0, "text": "मैं शुक्रवार तक योजना भेजूंगी।"}])
        self.assertEqual(result["actions"][0]["owner"], "Asha")

    def test_action_grounding_requires_exact_model_provided_evidence(self):
        transcript = [{"speaker": "Asha", "start": 12, "text": "I will send the revised launch plan."}]
        summary = {"actions": [
            {"text": "Send revised launch plan", "owner": "Asha", "due": "2026-08-25", "priority": "medium", "context": "", "evidence_quote": "I will send the revised launch plan.", "evidence_start": 12},
            {"text": "Design a better dashboard", "owner": "Asha", "due": None, "priority": "medium", "context": "", "evidence_quote": "The dashboard must be redesigned", "evidence_start": 12},
        ]}
        result = ground_actions(summary, transcript)
        self.assertEqual(result["actions"], [{
            "text": "Send revised launch plan", "owner": "Asha", "due": None, "priority": "medium", "context": "",
            "evidence_quote": "I will send the revised launch plan.", "evidence_start": 12,
        }])


if __name__ == "__main__":
    unittest.main()
