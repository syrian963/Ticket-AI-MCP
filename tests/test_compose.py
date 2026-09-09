# SPDX-License-Identifier: MIT

"""Writing a ticket with a model, and keeping the model on a short leash.

The model is faked here. What is being tested is everything around it: that the
prompt is built from measurements rather than from advice, that a draft is
marked against the corpus like any other, and that a revision which scores
worse is thrown away rather than shipped because it came second.
"""

from __future__ import annotations

import httpx
import pytest
from conftest import GOOD_BODY, make_detail, make_ticket

from ticket_ai_mcp.compose import build_prompt, compose
from ticket_ai_mcp.context import Context, PriorArt
from ticket_ai_mcp.mining import pick
from ticket_ai_mcp.profile import build
from ticket_ai_mcp.similar import Match
from ticket_ai_mcp.writers import WriterError, available
from ticket_ai_mcp.writers.openai_compatible import OllamaWriter, OpenAICompatible


@pytest.fixture
def profile():
    details = [
        make_detail(
            make_ticket(f"#{i}", description=GOOD_BODY, labels=("bug",), author=f"dev{i % 5}")
        )
        for i in range(20)
    ]
    taken, _ = pick(details, want=20)
    return build(taken, project="acme/shop", tracker="gitlab")


class Scripted:
    """A writer that returns whatever the test told it to, in order."""

    name = "scripted"
    model = "scripted-1"

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.calls: list[tuple[str, str]] = []

    def write(self, system: str, prompt: str) -> str:
        self.calls.append((system, prompt))
        return self.replies.pop(0) if self.replies else ""

    def models(self) -> list[str]:
        return ["scripted-1"]


class TestPrompt:
    def test_it_carries_measurements_not_advice(self, profile):
        # "78% of tickets here have this" is a fact about the team. "Tickets
        # should have acceptance criteria" is the advice the whole tool exists
        # to replace, and it must not creep back in through the prompt.
        prompt = build_prompt("Export is broken", profile)
        assert "## Acceptance criteria" in prompt
        assert "in 100%" in prompt or "goes with" in prompt
        assert "should have" not in prompt.lower()

    def test_related_tickets_and_touched_files_reach_the_model(self, profile):
        context = Context(
            subject="export",
            prior=(
                PriorArt(
                    match=Match(
                        ticket=make_ticket("#42", title="Etikettendruck bricht ab"),
                        score=0.9,
                        shared=("export",),
                    ),
                    files=("app/export.py",),
                ),
            ),
        )
        prompt = build_prompt("Export is broken", profile, context)
        assert "#42" in prompt
        assert "app/export.py" in prompt

    def test_labels_are_named_as_metadata_not_as_a_section(self, profile):
        # A live 3B model wrote a "## Labels" section into the body, because
        # the prompt listed the project's labels without saying what they were
        # for. Labels are tracker metadata; they are not part of a description.
        writer = Scripted(GOOD_BODY)
        compose("t", profile, writer)
        system = writer.calls[0][0]
        assert "never write a" in system.lower()
        assert "labels" in system.lower()

    def test_the_revision_keeps_the_draft_apart_from_the_feedback(self, profile):
        # A live model copied a finding's wording into the ticket - the phrase
        # "Anyone will know it did when they see..." is the text of a `fix`,
        # not anything about the bug. Fencing the draft and saying the
        # feedback is not text to reuse is what stopped it.
        writer = Scripted("kaputt", GOOD_BODY)
        compose("t", profile, writer, labels=("bug",))
        revision = writer.calls[1][1]
        assert "=== DRAFT ===" in revision
        assert "=== END DRAFT ===" in revision
        assert "none of the feedback wording belongs in it" in revision
        # The draft itself is inside the fence, the findings after it.
        assert revision.index("kaputt") < revision.index("What it got wrong:")

    def test_the_language_reaches_the_system_prompt(self, profile):
        writer = Scripted(GOOD_BODY)
        german = profile.with_language("de")
        compose("Titel", german, writer)
        assert "German" in writer.calls[0][0]


class TestCompose:
    def test_a_good_first_answer_is_not_revised(self, profile):
        writer = Scripted(GOOD_BODY)
        result = compose("Fix the label printing", profile, writer, labels=("bug",))
        assert result.attempts == 1
        assert result.review.findings == ()
        assert result.review.alignment == 1.0

    def test_a_poor_answer_gets_one_revision(self, profile):
        writer = Scripted("kaputt", GOOD_BODY)
        result = compose("Fix the label printing", profile, writer, labels=("bug",))
        assert result.attempts == 2
        assert result.body == GOOD_BODY.strip()
        assert result.review.findings == ()

    def test_the_revision_prompt_carries_the_findings_and_their_counts(self, profile):
        writer = Scripted("kaputt", GOOD_BODY)
        compose("Fix the label printing", profile, writer, labels=("bug",))
        revision = writer.calls[1][1]
        assert "exemplar" in revision
        assert "kaputt" in revision

    def test_a_worse_revision_is_discarded(self, profile):
        # A revision that misunderstood must not ship just because it came
        # second. That would make the loop actively harmful.
        writer = Scripted(GOOD_BODY.replace("- [ ] ", "- "), "nothing at all")
        result = compose("Fix the label printing", profile, writer, labels=("bug",))
        assert "Acceptance criteria" in result.body
        assert result.body != "nothing at all"

    def test_it_stops_after_the_configured_revisions(self, profile):
        writer = Scripted("bad", "still bad", "worse")
        result = compose("t", profile, writer, revisions=1)
        assert result.attempts == 2
        assert len(writer.calls) == 2


class TestBackends:
    def test_both_names_are_registered(self):
        assert available() == ("ollama", "openai")

    def test_ollama_needs_no_key_and_knows_where_to_look(self):
        writer = OllamaWriter()
        assert writer.base_url == "http://localhost:11434/v1"
        assert writer.model == "llama3.1"

    def test_the_generic_backend_refuses_to_guess(self):
        # No default provider and no default model: guessing at someone's
        # endpoint is worse than asking.
        with pytest.raises(WriterError, match="base url"):
            OpenAICompatible()
        with pytest.raises(WriterError, match="model"):
            OpenAICompatible(base_url="https://example.com/v1")
        with pytest.raises(WriterError, match="API key"):
            OpenAICompatible(base_url="https://example.com/v1", model="m")

    def test_it_speaks_the_chat_completions_protocol(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["body"] = request.read().decode()
            return httpx.Response(200, json={"choices": [{"message": {"content": "  a ticket  "}}]})

        writer = OllamaWriter(
            client=httpx.Client(
                base_url="http://localhost:11434/v1",
                transport=httpx.MockTransport(handler),
            )
        )
        assert writer.write("sys", "user") == "a ticket"
        assert seen["path"].endswith("/chat/completions")
        assert "llama3.1" in seen["body"]

    def test_a_local_server_that_is_not_running_says_so(self):
        def handler(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        writer = OllamaWriter(
            client=httpx.Client(
                base_url="http://localhost:11434/v1",
                transport=httpx.MockTransport(handler),
            )
        )
        with pytest.raises(WriterError, match="ollama serve"):
            writer.write("s", "p")

    def test_a_missing_model_names_the_fix(self):
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": "not found"})

        writer = OllamaWriter(
            client=httpx.Client(
                base_url="http://localhost:11434/v1",
                transport=httpx.MockTransport(handler),
            )
        )
        with pytest.raises(WriterError, match="ollama pull"):
            writer.write("s", "p")

    def test_models_are_listed_from_the_endpoint(self):
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [{"id": "b"}, {"id": "a"}, {"id": "a"}]})

        writer = OllamaWriter(
            client=httpx.Client(
                base_url="http://localhost:11434/v1",
                transport=httpx.MockTransport(handler),
            )
        )
        assert writer.models() == ["a", "b"]

    def test_an_endpoint_that_cannot_list_returns_nothing_not_a_guess(self):
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        writer = OllamaWriter(
            client=httpx.Client(
                base_url="http://localhost:11434/v1",
                transport=httpx.MockTransport(handler),
            )
        )
        assert writer.models() == []
