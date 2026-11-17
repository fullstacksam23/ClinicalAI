"""Offline checks for the Groq chat transport."""

import unittest
from unittest.mock import patch

import httpx
from server import chat_llm


class GroqChatTests(unittest.TestCase):
    def test_sends_schema_and_returns_json_object(self):
        response = httpx.Response(200, json={
            "choices": [{"message": {"content": '{"operation":"none"}'}}]
        }, request=httpx.Request("POST", chat_llm.GROQ_CHAT_URL))
        with patch.dict(chat_llm.os.environ, {"GROQ_API_KEY": "test-key"}), patch.object(
            chat_llm.httpx, "post", return_value=response
        ) as send:
            result = chat_llm.complete_json(
                model="openai/gpt-oss-20b",
                system="Plan retrieval",
                user="Show my reports",
                schema={"type": "object"},
                name="retrieval_plan",
            )

        self.assertEqual(result, {"operation": "none"})
        body = send.call_args.kwargs["json"]
        self.assertEqual(send.call_args.kwargs["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(body["messages"][0]["role"], "system")
        self.assertEqual(body["response_format"]["json_schema"]["name"], "retrieval_plan")

    def test_missing_key_fails_before_request(self):
        with patch.dict(chat_llm.os.environ, {"GROQ_API_KEY": ""}), patch.object(
            chat_llm.httpx, "post"
        ) as send:
            with self.assertRaisesRegex(RuntimeError, "GROQ_API_KEY is not set"):
                chat_llm.complete_json(
                    model="openai/gpt-oss-20b", system="x", user="y",
                    schema={"type": "object"}, name="test",
                )
        send.assert_not_called()

    def test_provider_error_code_is_reported_without_body(self):
        response = httpx.Response(403, json={"error": {
            "code": "model_permission_blocked_org", "message": "Sensitive detail"
        }}, request=httpx.Request("POST", chat_llm.GROQ_CHAT_URL))
        with patch.dict(chat_llm.os.environ, {"GROQ_API_KEY": "test-key"}), patch.object(
            chat_llm.httpx, "post", return_value=response
        ):
            with self.assertRaisesRegex(RuntimeError, "HTTP 403, model_permission_blocked_org") as error:
                chat_llm.complete_json(
                    model="openai/gpt-oss-20b", system="x", user="y",
                    schema={"type": "object"}, name="test",
                )
        self.assertNotIn("Sensitive detail", str(error.exception))


if __name__ == "__main__":
    unittest.main()
