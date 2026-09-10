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

from ticket_ai_mcp.compose import SYSTEM, build_prompt, compose
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

    def test_an_unknown_writer_lists_the_known_ones_and_the_way_out(self):
        # The typo case, and the only place that can say "you do not need one
        # of these at all" - which is true of everything except composing.
        from ticket_ai_mcp.writers import build as build_writer

        with pytest.raises(WriterError) as caught:
            build_writer("gpt5")
        message = str(caught.value)
        assert "ollama" in message and "openai" in message
        assert "TICKET_AI_WRITER" in message

    def test_building_a_known_one_returns_it(self):
        from ticket_ai_mcp.writers import build as build_writer

        assert build_writer("ollama").name == "ollama"

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


class TestABoardThatWritesProse:
    """Three of the forty-three boards in the fleet use no template at all.

    The prompt used to head the section list with "Sections to use, and why
    each one:" and then, when there were none, print "(no recurring sections)"
    underneath it - a heading promising a list followed by no list, while the
    system prompt separately said to use exactly the sections it was given. A
    model handed that contradiction resolves it the way models do.
    """

    def prose_profile(self):
        # Long enough not to be excluded, and no headings anywhere.
        body = (
            "The importer stops after the first batch and writes nothing further, "
            "which nobody notices until the nightly totals are short. It should "
            "either finish the run or fail loudly enough to page someone."
        )
        details = [
            make_detail(make_ticket(f"#{i}", description=body, author=f"dev{i % 5}"))
            for i in range(20)
        ]
        taken, _ = pick(details, want=20)
        return build(taken, project="acme/shop", tracker="jira")

    def test_the_prompt_does_not_promise_a_list_it_has_not_got(self):
        profile = self.prose_profile()
        assert profile.skeleton() == ()
        prompt = build_prompt("Importer stops after the first batch", profile)
        assert "Sections to use" not in prompt
        assert "no headings at all" in prompt

    def test_a_board_with_a_template_still_gets_its_list(self):
        details = [
            make_detail(make_ticket(f"#{i}", description=GOOD_BODY, author=f"dev{i % 5}"))
            for i in range(20)
        ]
        taken, _ = pick(details, want=20)
        prompt = build_prompt("Label printing stops", build(taken, project="p", tracker="gitlab"))
        assert "Sections to use" in prompt
        assert "no headings at all" not in prompt

    def test_the_system_prompt_names_the_prose_case(self):
        # Rule 2 tells the model to use exactly the sections it is given.
        # Without this clause, "exactly none" is left to interpretation.
        assert "prose" in SYSTEM


class TestTheTitleDoesNotComeBackTwice:
    """The body goes in the description field, under the title already.

    Rule 5 says to write the description only. Asked for a German ticket on a
    board whose template is English, llama3.2 opened with the ticket title
    underlined in `=` regardless - and that copy would sit in every ticket the
    tool writes, directly below the same words.
    """

    def compose_with(self, reply: str, title: str = "Export nach PDF verliert Verläufe"):
        details = [
            make_detail(make_ticket(f"#{i}", description=GOOD_BODY, author=f"d{i % 4}"))
            for i in range(20)
        ]
        taken, _ = pick(details, want=20)
        profile = build(taken, project="acme/shop", tracker="gitlab")
        return compose(title, profile, Scripted(reply), revisions=0)

    def test_a_setext_title_is_taken_off(self):
        body = self.compose_with(
            "Export nach PDF verliert Verläufe\n=====\n\n## Problem\nEs bricht ab."
        ).body
        assert body.startswith("## Problem")

    def test_a_hash_title_is_taken_off_too(self):
        body = self.compose_with(
            "# Export nach PDF verliert Verläufe\n\n## Problem\nEs bricht."
        ).body
        assert body.startswith("## Problem")

    def test_a_bold_title_is_taken_off_as_well(self):
        # What llama3.2 actually produced on the second live run, after being
        # told twice not to. Bold rather than underlined, and it falls out of
        # the same normalisation the headings use.
        body = self.compose_with(
            "**Export nach PDF verliert Verläufe**\n\n## Problem\nEs bricht ab."
        ).body
        assert body.startswith("## Problem")

    def test_punctuation_and_case_do_not_save_it(self):
        body = self.compose_with("## EXPORT NACH PDF VERLIERT VERLÄUFE!\n\n## Problem\nx").body
        assert body.startswith("## Problem")

    def test_an_opening_sentence_of_its_own_stays(self):
        # Only a first line that *is* the title comes off. Anything else is
        # the model writing, and this must not eat it.
        body = self.compose_with("## Problem\nDer Export bricht ab.").body
        assert body.startswith("## Problem")
        assert "Der Export bricht ab." in body

    def test_a_first_line_that_merely_mentions_the_title_stays(self):
        body = self.compose_with("Beim Export nach PDF verliert Inkscape die Verläufe.").body
        assert body.startswith("Beim Export")


class TestHeadingsAreNotTranslated:
    def test_rule_one_exempts_the_given_headings(self):
        # A German ticket on a board whose template is English: rule 1 said to
        # write every heading in German and rule 2 said to copy the headings
        # exactly. A model that resolves that the other way translates
        # "What happened?" into a section the team does not have, and every
        # section check then fails on a draft that is actually fine.
        assert "copied exactly" in SYSTEM
        assert "another language" in SYSTEM


class TestWhatTheEndpointSaysWhenItRefuses:
    """The messages that decide whether somebody can fix their own setup.

    Composing is the one feature that talks to a service the user configured,
    so every way it can refuse is a support conversation. These paths had no
    tests: the four status codes and a body that is not what the protocol
    promises.
    """

    def writer(self, status: int, body=None, *, ollama: bool = False):
        def handler(_: httpx.Request) -> httpx.Response:
            if isinstance(body, str):
                return httpx.Response(status, text=body)
            return httpx.Response(status, json=body if body is not None else {})

        transport = httpx.MockTransport(handler)
        if ollama:
            return OllamaWriter(
                client=httpx.Client(base_url="http://localhost:11434/v1", transport=transport)
            )
        return OpenAICompatible(
            base_url="https://api.example.com/v1",
            model="m",
            api_key="k",
            client=httpx.Client(base_url="https://api.example.com/v1", transport=transport),
        )

    def test_a_rate_limit_says_to_wait_rather_than_to_check_the_key(self):
        # The same distinction the trackers make: a 429 is a wait, not a
        # configuration mistake, and sending someone to their key wastes the
        # only thing they have - patience.
        with pytest.raises(WriterError, match="rate limiting"):
            self.writer(429).write("s", "p")

    def test_a_rejected_key_names_the_variable_to_change(self):
        for status in (401, 403):
            with pytest.raises(WriterError, match="TICKET_AI_API_KEY"):
                self.writer(status).write("s", "p")

    def test_any_other_failure_quotes_what_came_back(self):
        # Anything unhandled has to carry the server's own words, or the user
        # is debugging with a number.
        with pytest.raises(WriterError, match="upstream exploded"):
            self.writer(502, "upstream exploded").write("s", "p")

    def test_a_reply_that_is_not_the_protocol_is_not_a_crash(self):
        # A proxy that answers 200 with an error object, which is common
        # enough that a KeyError here would be a regular occurrence.
        with pytest.raises(WriterError, match="unexpected answer"):
            self.writer(200, {"error": "quota exceeded"}).write("s", "p")

    def test_a_missing_model_on_a_remote_endpoint_does_not_suggest_ollama(self):
        # `ollama pull` is not a thing you can do to somebody's API.
        with pytest.raises(WriterError) as caught:
            self.writer(404).write("s", "p")
        assert "ollama pull" not in str(caught.value)
        assert "ticket-ai models" in str(caught.value)

    def test_a_listing_that_is_not_a_list_returns_nothing(self):
        assert self.writer(200, {"data": "not a list"}).models() == []
        assert self.writer(200, "plain text, not json").models() == []
