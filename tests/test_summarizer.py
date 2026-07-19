import json
import unittest

from meeter.summarizer import fallback_summary, parse_model_json, summarize


class SummarizerTests(unittest.TestCase):
    def test_model_json_is_normalized(self):
        raw = """```json
        {"title":"Launch sync","summary":"We aligned.","decisions":["Ship Friday"],
        "actions":[{"text":"Send the plan","owner":"Asha","due":"2026-07-20","priority":"URGENT"}],
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


if __name__ == "__main__":
    unittest.main()
