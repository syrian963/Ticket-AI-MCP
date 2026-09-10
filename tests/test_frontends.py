# SPDX-License-Identifier: MIT

"""Both front ends, driven in-process against a tracker that never leaves memory.

The whole pipeline runs here - mine, profile, cache, review - because the parts
that break in practice are the seams between modules, and unit tests by
construction never touch a seam. The CLI and the MCP server are exercised the
way a user and an assistant actually reach them: argv in, text out; tool name
in, tool result out.
"""

from __future__ import annotations

import io
import re
import shlex

import anyio
import pytest
from conftest import GOOD_BODY, make_detail, make_ticket

from ticket_ai_mcp import cli, server
from ticket_ai_mcp.config import Settings, settings, tracker_for, writer_for
from ticket_ai_mcp.schemas import TicketQuery
from ticket_ai_mcp.trackers import TrackerError, register
from ticket_ai_mcp.trackers.base import _REGISTRY


class FakeTracker:
    """Twenty-four tickets that all follow the same template, plus two that do not."""

    name = "fake"

    def __init__(self, **_):
        self.details = {}
        for i in range(24):
            detail = make_detail(
                make_ticket(
                    f"#{i}",
                    description=GOOD_BODY,
                    labels=("bug", "team::shop"),
                    author=f"dev{i % 6}",
                )
            )
            self.details[detail.ticket.key] = detail
        for key, body in (("#900", "kaputt"), ("#901", "")):
            detail = make_detail(make_ticket(key, description=body, state="open", labels=()))
            self.details[detail.ticket.key] = detail

    def search(self, query: TicketQuery):
        want = "closed" if query.state == "closed" else "open"
        return [d.ticket for d in self.details.values() if d.ticket.state == want][: query.limit]

    def fetch(self, project: str, key: str):
        return self.details[key].ticket

    def detail(self, ticket):
        return self.details[ticket.key]


@pytest.fixture
def workspace(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TICKET_AI_CACHE_DIR", str(tmp_path))
    # Registered here rather than with a decorator on the class. A decorator
    # runs when pytest *imports* this module, which is during collection - so
    # a fake adapter was in the registry for every test in the session, and
    # `teardown_module` only took it out again once this file was finished.
    # Anything that asked which trackers exist before then saw four.
    register(FakeTracker)
    monkeypatch.setenv("TICKET_AI_TRACKER", "fake")
    monkeypatch.setenv("TICKET_AI_PROJECT", "acme/shop")
    capsys.readouterr()
    return tmp_path


def run(argv: list[str], capsys) -> tuple[int, str]:
    """Run the CLI and return its exit code with everything it printed.

    Both streams. This returned stdout alone, which meant no test here could
    check an error message - and the CLI puts every one of them on stderr,
    where they belong. A helper that drops half the output quietly excludes
    the half most worth asserting on.
    """
    code = cli.main(argv)
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def call(name: str, **arguments) -> str:
    """Call an MCP tool the way a client would, and flatten the result to text."""
    result = anyio.run(lambda: server.server.call_tool(name, arguments))
    return "\n".join(
        block.text for block in getattr(result, "content", []) if hasattr(block, "text")
    )


class TestCli:
    def test_learn_then_style_then_review(self, workspace, capsys):
        code, out = run(["learn"], capsys)
        assert code == 0
        assert "Acceptance criteria" in out
        assert "24" in out
        assert (workspace / "profile-fake-acme-shop.json").exists()

        code, out = run(["style"], capsys)
        assert code == 0
        assert "## The template" in out

        # #900 is the stub. Its findings have to quote the corpus, not a
        # hardcoded threshold.
        code, out = run(["review", "#900"], capsys)
        assert "missing" in out.lower() or "No 'Acceptance criteria'" in out
        assert "of the 24 exemplar" in out

    def test_review_before_learn_says_what_to_run(self, workspace, capsys):
        code = cli.main(["review", "#900"])
        assert code == 2
        assert "ticket-ai learn" in capsys.readouterr().err

    def test_fail_under_is_usable_in_ci(self, workspace, capsys):
        run(["learn"], capsys)
        assert run(["review", "#900", "--fail-under", "0.9"], capsys)[0] == 1
        assert run(["review", "#0", "--fail-under", "0.9"], capsys)[0] == 0

    def test_draft_reads_the_body_from_a_file(self, workspace, capsys, tmp_path):
        run(["learn"], capsys)
        body = tmp_path / "draft.md"
        body.write_text("Etikettendruck kaputt.\n", encoding="utf-8")
        code, out = run(["draft", "--title", "Etikettendruck kaputt", "--file", str(body)], capsys)
        assert code == 0
        assert "(draft)" in out
        assert "of the 24 exemplar" in out

    def test_draft_reads_the_body_from_stdin(self, workspace, capsys, monkeypatch):
        run(["learn"], capsys)
        monkeypatch.setattr("sys.stdin", io.StringIO(GOOD_BODY))
        code, out = run(["draft", "--title", "Fix the label printing"], capsys)
        assert code == 0
        assert "(draft)" in out

    def test_draft_can_gate(self, workspace, capsys, monkeypatch):
        run(["learn"], capsys)
        monkeypatch.setattr("sys.stdin", io.StringIO("kaputt"))
        assert run(["draft", "--title", "x", "--fail-under", "0.9"], capsys)[0] == 1

    def test_open_lists_worst_first(self, workspace, capsys):
        run(["learn"], capsys)
        code, out = run(["open"], capsys)
        assert code == 0
        assert "#901" in out and "#900" in out
        # Alignment floors at 0, so these two tie. The tie has to break on the
        # ticket key rather than on dict order: a worklist that reshuffles
        # itself between runs is one nobody can work through.
        assert out.index("#900") < out.index("#901")
        assert "no description at all" in out

    def test_learning_from_named_tickets_skips_the_heuristic(self, workspace, capsys):
        # The caller's judgement outranks the ranking: #900 is a stub and mining
        # would drop it, but naming it means it counts.
        code, out = run(["learn", "--from", "#0,#900"], capsys)
        assert "Built from 2 tickets" in out
        assert "#900" in out
        # Two exemplars is not a corpus, and the exit code has to say so even
        # though the profile was written.
        assert code == 1

    def test_an_unknown_tracker_lists_the_known_ones(self, capsys, monkeypatch):
        monkeypatch.setenv("TICKET_AI_PROJECT", "x")
        code = cli.main(["--project", "x", "learn"])
        assert code == 2
        assert "TICKET_AI_TRACKER" in capsys.readouterr().err


class TestMcpServer:
    def test_tools_are_all_exposed(self):
        names = {t.name for t in anyio.run(server.server.list_tools)}
        assert names == {
            "learn_conventions",
            "house_style",
            "ticket_template",
            "ticket_context",
            "template_gaps",
            "review_draft",
            "review_ticket",
            "review_open_tickets",
        }

    def test_the_instructions_send_the_draft_through_review(self):
        # Writing a ticket and handing it over unchecked wastes the one moment
        # when fixing it is free.
        assert "review_draft" in server.INSTRUCTIONS

    def test_the_instructions_say_who_writes_the_ticket(self):
        # The failure mode this guards against: a model that calls the two
        # tools and pastes their output at the user instead of writing
        # anything.
        assert "write it yourself" in server.INSTRUCTIONS
        assert "They asked for a ticket" in server.INSTRUCTIONS

    def test_house_style_before_learning_is_an_answer_not_an_error(self, workspace):
        out = call("house_style")
        assert "learn_conventions" in out

    def test_the_whole_flow(self, workspace):
        assert "Acceptance criteria" in call("learn_conventions")
        assert "## The template" in call("house_style")

        template = call("ticket_template")
        assert "## Acceptance criteria" in template
        assert "characters" in template

        review = call("review_ticket", ticket="#900")
        assert "of the 24 exemplar" in review

        batch = call("review_open_tickets")
        assert "#900" in batch

    def test_the_instructions_warn_that_alignment_is_not_quality(self):
        # The one thing a model reading this server can most easily get wrong.
        assert "not quality" in server.INSTRUCTIONS


def teardown_module(_):
    # Belt as well as braces: the fixture scopes it, this catches a test that
    # registers one some other way.
    _REGISTRY.pop("fake", None)


class TestTheWorkflowSnippet:
    """The Actions workflow this prints is pasted, not run from here.

    Which makes it the one piece of output that can rot silently: a flag
    renamed in `cli.py` breaks a file already sitting in somebody's
    repository, and nothing here would fail. A flag *was* renamed the day
    these tests were written - `--want` became `--keep` - which is what
    prompted them.
    """

    def snippet(self) -> str:
        from ticket_ai_mcp.workflow import snippet

        return snippet()

    def commands(self) -> list[list[str]]:
        """Every `ticket-ai-mcp ...` invocation in the snippet, as argv.

        The workflow writes them across continuation lines, so the backslashes
        are joined up before splitting.
        """
        joined = re.sub(r"\\s*\n\s*", " ", self.snippet())
        found = []
        for line in joined.splitlines():
            if "ticket-ai-mcp " not in line:
                continue
            argv = shlex.split(line.split("ticket-ai-mcp ", 1)[1])
            # The GitHub expressions are values, not flags; a placeholder
            # keeps argparse happy without pretending to evaluate them.
            found.append(["title" if a.startswith("${{") else a for a in argv])
        return found

    def test_it_actually_invokes_something(self):
        assert len(self.commands()) >= 2

    def test_every_command_in_it_still_parses(self):
        parser = cli.build_parser()
        for argv in self.commands():
            # Raises SystemExit on an unknown flag, which is the failure this
            # is here to catch.
            parser.parse_args(argv)

    def test_the_github_expressions_survived_the_f_string(self):
        # Doubling braces inside an f-string is easy to get wrong by one, and
        # `${{{ }}}` is a workflow GitHub rejects.
        text = self.snippet()
        assert "${{ github.repository }}" in text
        assert "${{ secrets.GITHUB_TOKEN }}" in text
        assert "${{{" not in text

    def test_it_only_touches_an_issue_with_an_empty_body(self):
        # The guard that keeps it from overwriting somebody's actual ticket.
        assert "github.event.issue.body == ''" in self.snippet()

    def test_the_model_and_endpoint_are_the_ones_asked_for(self):
        from ticket_ai_mcp.workflow import snippet

        text = snippet(model="acme/tiny", base_url="https://acme.example/v1")
        assert "TICKET_AI_MODEL: acme/tiny" in text
        assert "TICKET_AI_BASE_URL: https://acme.example/v1" in text


class TestTheFirstRun:
    """The messages somebody sees before the tool has ever worked for them.

    Every one of these is the whole of what a new user has to go on, and none
    of them had a test. The module's own docstring says they get more care
    than the code does; that is only true if something holds them to it.
    """

    def clear(self, monkeypatch):
        for name in (
            "TICKET_AI_TRACKER",
            "TICKET_AI_PROJECT",
            "TICKET_AI_GITLAB_URL",
            "TICKET_AI_GITLAB_TOKEN",
            "TICKET_AI_JIRA_URL",
            "TICKET_AI_GITHUB_TOKEN",
            "TICKET_AI_WRITER",
        ):
            monkeypatch.delenv(name, raising=False)

    def test_no_tracker_names_the_three_it_knows(self, monkeypatch):
        self.clear(monkeypatch)
        with pytest.raises(TrackerError) as caught:
            settings()
        assert "TICKET_AI_TRACKER" in str(caught.value)
        for name in ("gitlab", "jira", "github"):
            assert name in str(caught.value)

    def test_no_project_shows_what_one_looks_like_on_each(self, monkeypatch):
        # "Set TICKET_AI_PROJECT" is useless on its own: the answer is a path
        # on GitLab, a key on Jira and owner/repo on GitHub, and a person who
        # does not know that is exactly who is reading this.
        self.clear(monkeypatch)
        with pytest.raises(TrackerError) as caught:
            settings(tracker="gitlab")
        message = str(caught.value)
        assert "acme/shop" in message and "PROJ" in message and "owner/repo" in message

    def test_an_argument_beats_the_environment(self, monkeypatch):
        # One server, several projects in one session - the normal case for an
        # assistant that has just been asked about a different repository.
        self.clear(monkeypatch)
        monkeypatch.setenv("TICKET_AI_TRACKER", "gitlab")
        monkeypatch.setenv("TICKET_AI_PROJECT", "acme/shop")
        assert settings("github", "other/repo") == Settings("github", "other/repo")

    def test_gitlab_needs_a_url_and_says_what_one_looks_like(self, monkeypatch):
        self.clear(monkeypatch)
        with pytest.raises(TrackerError) as caught:
            tracker_for("gitlab")
        assert "TICKET_AI_GITLAB_URL" in str(caught.value)
        assert "gitlab.com" in str(caught.value)

    def test_gitlab_does_not_need_a_token(self, monkeypatch):
        # A public project answers without one, and refusing to start without
        # it shut the tool out of every open-source board.
        self.clear(monkeypatch)
        monkeypatch.setenv("TICKET_AI_GITLAB_URL", "https://gitlab.com")
        assert tracker_for("gitlab").anonymous is True

    def test_github_says_which_access_the_token_needs(self, monkeypatch):
        self.clear(monkeypatch)
        with pytest.raises(TrackerError) as caught:
            tracker_for("github")
        assert "TICKET_AI_GITHUB_TOKEN" in str(caught.value)
        assert "issues" in str(caught.value)

    def test_no_model_configured_is_not_a_fault(self, monkeypatch):
        # Everything except composing works without one, so this returns None
        # rather than raising. Treating the normal state as an error would
        # make the tool feel broken to everyone who never wanted a model.
        self.clear(monkeypatch)
        assert writer_for() is None
        monkeypatch.setenv("TICKET_AI_WRITER", "none")
        assert writer_for() is None

    def test_a_slug_survives_a_project_path(self, monkeypatch):
        # It becomes a filename, and a GitLab path has slashes in it.
        assert Settings("gitlab", "acme/sub/shop").slug == "gitlab-acme-sub-shop"
        assert Settings("jira", "PROJ").slug == "jira-PROJ"


class TestEveryToolBeforeAnythingIsLearned:
    """The first mistake an assistant makes with this server.

    One of the eight tools had a test for it. Driving all eight against an
    empty cache found the one that answered as if nothing were missing.
    """

    def test_the_tools_that_need_a_profile_all_name_the_one_that_builds_it(self, workspace):
        # Five raise and one returns, which is fine - a client shows both to
        # the user. What matters is that all six say the same thing to do.
        for name, kwargs in (
            ("house_style", {}),
            ("ticket_template", {}),
            ("template_gaps", {}),
            ("review_draft", {"title": "t", "description": "d"}),
            ("review_ticket", {"ticket": "#900"}),
            ("review_open_tickets", {"limit": 2}),
        ):
            try:
                out = call(name, **kwargs)
            except Exception as exc:
                out = str(exc)
            assert "learn_conventions" in out, f"{name} did not say what to do: {out[:120]}"

    def test_context_says_it_has_no_house_style_behind_it(self, workspace):
        # It works without one - the prior art comes from the tracker - but it
        # used to say nothing, so a caller could not tell a context measured
        # against the team from one measured against nothing. The note fired
        # only for a profile that existed and was empty, which is the rarer
        # half of the same condition.
        out = call("ticket_context", subject="label printing")
        assert "learn_conventions" in out


class TestARefusalReachesTheCaller:
    """The MCP layer keeps a crash's text on the server. A refusal is not one.

    Measured against the real server: five of the eight tools raised
    `TrackerError` the first time anyone called them, and the client got
    `Error executing tool ticket_template` with the sentence naming
    `learn_conventions` left behind. Every message about a rate limit, a moved
    repository or a board with issues switched off went the same way.

    `house_style` was the exception, because it returned its message instead
    of raising - which is why it was the only one anybody ever saw.
    """

    def test_a_tracker_refusal_comes_back_as_text(self, workspace):
        # Through `call_tool`, the way a client reaches it - not by calling the
        # function, which is what made this invisible for so long.
        out = call("ticket_template")
        assert "learn_conventions" in out

    def test_every_tool_that_can_refuse_returns_rather_than_raises(self, workspace):
        for name, kwargs in (
            ("house_style", {}),
            ("ticket_template", {}),
            ("template_gaps", {}),
            ("review_draft", {"title": "t", "description": "d"}),
            ("review_ticket", {"ticket": "#900"}),
            ("review_open_tickets", {"limit": 2}),
            ("ticket_context", {"subject": "label printing"}),
        ):
            out = call(name, **kwargs)
            assert "learn_conventions" in out, f"{name}: {out[:120]}"

    def test_a_real_bug_still_looks_like_one(self, workspace, monkeypatch):
        # The decorator must not turn every failure into a polite paragraph.
        # An AttributeError is a defect and has to keep behaving like one.
        import ticket_ai_mcp.server as server_module

        def explode(*_args, **_kwargs):
            raise AttributeError("something is actually broken")

        monkeypatch.setattr(server_module, "_need", explode)
        with pytest.raises(Exception, match="Error executing tool"):
            call("ticket_template")

    def test_the_signatures_survive_the_decorator(self):
        # It wraps every tool, so a mangled signature would silently empty the
        # schema a client reads to know how to call anything.
        tools = {t.name: t for t in anyio.run(server.server.list_tools)}
        params = sorted((tools["review_draft"].input_schema or {}).get("properties", {}))
        assert params == ["description", "labels", "project", "title", "tracker"]
        assert "ctx" not in (tools["learn_conventions"].input_schema or {}).get("properties", {})


class TestTheOptionalFourthMethod:
    def test_an_adapter_without_changed_files_still_works(self):
        # The protocol calls it optional and says callers treat it as a bonus.
        # `gather` called it anyway, so a tracker implementing only the three
        # required methods took `ticket_context` down with an AttributeError -
        # reported to the client as "Error executing tool", cause discarded.
        from ticket_ai_mcp.context import gather
        from ticket_ai_mcp.schemas import LinkedChange, TicketDetail

        class ThreeMethods:
            name = "minimal"

            def search(self, query):
                return [make_ticket("#1", title="Etikettendruck", description=GOOD_BODY)]

            def fetch(self, project, key):  # pragma: no cover - unused
                raise NotImplementedError

            def detail(self, ticket):
                return TicketDetail(
                    ticket=ticket,
                    linked_changes=(
                        LinkedChange(ref="!1", title="t", url="u", state="merged", merged=True),
                    ),
                )

        found = gather("Etikettendruck", ThreeMethods(), "acme/shop")
        assert found.prior
        assert found.prior[0].files == ()


class TestListingModelsWhenThereAreNone:
    """The message somebody gets before they have set anything up.

    Defaulting to a local Ollama is right for a question like "what can I
    use?" - it is the backend that needs no key. What was missing is which
    backend that was: with nothing configured, the message talked about a key
    that had never been set, for an endpoint it did not name.
    """

    def test_it_names_the_endpoint_it_tried(self, capsys, monkeypatch):
        monkeypatch.delenv("TICKET_AI_WRITER", raising=False)
        monkeypatch.setenv("TICKET_AI_BASE_URL", "http://localhost:11434/v1")
        code, out = run(["models"], capsys)
        assert code == 1
        assert "11434" in out
        assert "ollama" in out

    def test_with_nothing_set_it_says_what_it_assumed(self, capsys, monkeypatch):
        monkeypatch.delenv("TICKET_AI_WRITER", raising=False)
        _, out = run(["models"], capsys)
        assert "Nothing is configured" in out
        assert "TICKET_AI_WRITER" in out

    def test_with_a_backend_set_it_does_not_talk_about_ollama(self, capsys, monkeypatch):
        # Someone who configured a hosted endpoint does not need to be told
        # about a local one they did not ask for.
        monkeypatch.setenv("TICKET_AI_WRITER", "openai")
        # Port 9 is discard: the connection is refused at once. A test that
        # reaches for a real hostname is a test that needs a network, which is
        # the thing the fleet harness exists to keep out of this suite - and
        # api.example.com added ten seconds to it.
        monkeypatch.setenv("TICKET_AI_BASE_URL", "http://127.0.0.1:9/v1")
        monkeypatch.setenv("TICKET_AI_MODEL", "m")
        monkeypatch.setenv("TICKET_AI_API_KEY", "k")
        code, out = run(["models"], capsys)
        assert code == 1
        assert "127.0.0.1:9" in out
        assert "Nothing is configured" not in out
