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

import anyio
import pytest
from conftest import GOOD_BODY, make_detail, make_ticket

from ticket_ai_mcp import cli, server
from ticket_ai_mcp.schemas import TicketQuery
from ticket_ai_mcp.trackers import register
from ticket_ai_mcp.trackers.base import _REGISTRY


@register
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
    monkeypatch.setenv("TICKET_AI_TRACKER", "fake")
    monkeypatch.setenv("TICKET_AI_PROJECT", "acme/shop")
    capsys.readouterr()
    return tmp_path


def run(argv: list[str], capsys) -> tuple[int, str]:
    code = cli.main(argv)
    return code, capsys.readouterr().out


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
    _REGISTRY.pop("fake", None)
