"""Tests for the Flask app, using fake models so no Ollama server is needed.

    python -m unittest tests/test_web.py
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from langchain_core.embeddings import Embeddings
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from agent import SiteAgent
from models import AgentAnswer, AgentConfig
from web import MAX_MESSAGE_CHARS, create_app


class KeywordEmbeddings(Embeddings):
    WORDS = ["founded", "anvils", "1999", "pricing"]

    def embed_documents(self, texts):
        return [[float(w in t.lower()) + 0.01 for w in self.WORDS] for t in texts]

    def embed_query(self, text):
        return self.embed_documents([text])[0]


class FakeLLM(GenericFakeChatModel):
    """Answers from the prompt text; rewrites follow-ups; can be told to fail."""
    fail: bool = False

    def with_structured_output(self, schema, **kw):
        def respond(prompt_value):
            if self.fail:
                raise ConnectionError("model server down")
            text = prompt_value.to_string()
            found = "1999" in text
            return AgentAnswer(answer="Founded in 1999." if found else "I could not find that.",
                               found_in_context=found)
        return RunnableLambda(respond)


def make_agent(llm=None):
    tmp = tempfile.mkdtemp()
    (Path(tmp) / "about.md").write_text(
        "---\nsource: https://acme.test/about\n---\n\n# About\n\nAcme was founded in 1999 and sells anvils.\n")
    llm = llm or FakeLLM(messages=iter([AIMessage(content="When was Acme founded?")] * 50))
    return SiteAgent(AgentConfig(data_dir=Path(tmp)), llm=llm, embeddings=KeywordEmbeddings())


class WebTests(unittest.TestCase):
    def setUp(self):
        self.agent = make_agent()
        self.app = create_app(self.agent, site_name="acme.test")
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def chat(self, msg, client=None):
        return (client or self.client).post("/api/chat", json={"message": msg})

    def test_index_renders(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"acme.test", r.data)

    def test_answer_with_sources(self):
        r = self.chat("When was Acme founded?")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["answer"], "Founded in 1999.")
        self.assertTrue(body["found_in_context"])
        self.assertEqual(body["sources"], ["https://acme.test/about"])

    def test_unanswerable_has_no_sources(self):
        tmp = tempfile.mkdtemp()
        (Path(tmp) / "x.md").write_text("---\nsource: https://acme.test/x\n---\n\nNothing relevant here.\n")
        agent = SiteAgent(AgentConfig(data_dir=Path(tmp)),
                          llm=FakeLLM(messages=iter([])), embeddings=KeywordEmbeddings())
        body = create_app(agent).test_client().post("/api/chat", json={"message": "Who?"}).get_json()
        self.assertFalse(body["found_in_context"])
        self.assertEqual(body["sources"], [])  # sources are only shown for grounded answers

    def test_rejects_bad_input(self):
        self.assertEqual(self.client.post("/api/chat", json={}).status_code, 400)
        self.assertEqual(self.client.post("/api/chat", json={"message": 5}).status_code, 400)
        self.assertEqual(self.chat("   ").status_code, 400)
        self.assertEqual(self.chat("x" * (MAX_MESSAGE_CHARS + 1)).status_code, 413)
        # Non-JSON content type must not be accepted (CSRF guard).
        r = self.client.post("/api/chat", data="message=hi",
                             content_type="application/x-www-form-urlencoded")
        self.assertEqual(r.status_code, 400)

    def test_history_is_per_session_and_resettable(self):
        other = self.app.test_client()
        self.chat("When was Acme founded?")
        self.chat("And anything else?")
        self.assertEqual(len(self.agent.history), 0)  # the shared base agent is never mutated
        # Second visitor starts fresh: their first question must not trigger the rewrite path.
        r = self.chat("When was Acme founded?", client=other)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.post("/api/reset").get_json(), {"ok": True})

    def test_model_failure_returns_502_without_details(self):
        agent = make_agent(FakeLLM(messages=iter([]), fail=True))
        client = create_app(agent).test_client()
        with self.assertLogs(level="ERROR"):
            r = client.post("/api/chat", json={"message": "hi"})
        self.assertEqual(r.status_code, 502)
        self.assertNotIn("model server down", r.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
