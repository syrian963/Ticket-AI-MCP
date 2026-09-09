# SPDX-License-Identifier: MIT

"""Gathering the evidence for a ticket that has not been written yet.

The thing being protected here is the division of labour. This half is
deterministic - the same subject gives the same files and the same prior art,
every time, with no model and no key - and the moment it starts guessing at
meaning it stops being checkable.
"""

from __future__ import annotations

from conftest import make_detail, make_ticket

from ticket_ai_mcp.codebase import search as search_code
from ticket_ai_mcp.context import gather
from ticket_ai_mcp.report import render_context
from ticket_ai_mcp.schemas import LinkedChange, TicketDetail
from ticket_ai_mcp.similar import rank, tokens


class FakeTracker:
    name = "fake"

    def __init__(self, details, files=None):
        self.details = {d.ticket.key: d for d in details}
        self.files = files or {}
        self.file_calls = 0

    def search(self, query):
        return [d.ticket for d in self.details.values()][: query.limit]

    def fetch(self, project, key):
        return self.details[key].ticket

    def detail(self, ticket):
        return self.details[ticket.key]

    def changed_files(self, project, change):
        self.file_calls += 1
        return tuple(self.files.get(change.ref, ()))


class TestTokens:
    def test_identifiers_split_into_words(self):
        # So a subject in prose matches a ticket that quotes a function name.
        assert tokens("getDestinationList") == ["get", "destination", "list"]
        assert tokens("destination_filter.py") == ["destination", "filter", "py"]

    def test_function_words_are_dropped(self):
        assert "der" not in tokens("der Export ist kaputt")
        assert "export" in tokens("der Export ist kaputt")


class TestSimilarity:
    def build(self):
        return [
            make_ticket("#1", title="Wareneingang schlaegt fehl", description="Die Seite"),
            make_ticket("#2", title="Etikettendruck Seite langsam", description="Die Seite"),
            make_ticket("#3", title="Seite laedt nicht", description="Die Seite ist da"),
        ]

    def test_a_rare_word_outranks_a_common_one(self):
        # Every ticket on this board says "Seite". The match should be driven
        # by the word that actually distinguishes one.
        found = rank("Etikettendruck Seite", self.build())
        assert found[0].ticket.key == "#2"
        assert "etikettendruck" in found[0].shared

    def test_it_names_the_words_that_matched(self):
        # So nobody has to trust the number: a match on "etikettendruck" is
        # obviously right, a match on "seite" obviously is not.
        assert all(m.shared for m in rank("Wareneingang", self.build()))

    def test_two_words_beat_one_however_rare_the_one_is(self):
        # Found on a real German board. A subject of three words returned
        # three tickets matching only on "Fehler" - a word half the board
        # uses - ranked above the one ticket that actually said
        # "Wareneingang". Agreement across terms is evidence of its own.
        tickets = [
            make_ticket("#1", title="Bug: Fehler in Kalkulation", description="Fehler"),
            make_ticket("#2", title="Fehler beim PDF", description="Fehler tritt auf"),
            make_ticket("#3", title="Fehler im Filter", description="Ein Fehler"),
            make_ticket("#4", title="Feedback Mira", description="Wareneingang Formular"),
            make_ticket("#5", title="Etwas ganz anderes", description="Etwas anderes"),
        ]
        found = rank("Wareneingang Formular Fehler", tickets)
        assert found[0].ticket.key == "#4"

    def test_weak_matches_are_dropped_rather_than_listed(self):
        # Five results of which four are wrong is worse than one result: it
        # teaches people to skim the section.
        tickets = [
            make_ticket("#1", title="Etikettendruck Anhang Upload", description="Etikettendruck"),
            *[
                make_ticket(f"#{i}", title="Seite laedt", description="Die Seite")
                for i in range(2, 8)
            ],
        ]
        found = rank("Etikettendruck Anhang Upload Seite", tickets)
        assert [m.ticket.key for m in found] == ["#1"]

    def test_no_overlap_is_no_match(self):
        assert rank("Zeiterfassung Urlaubsantrag", self.build()) == []

    def test_an_empty_subject_returns_nothing(self):
        assert rank("", self.build()) == []
        assert rank("der die das", self.build()) == []


class TestCodebaseSearch:
    def repo(self, tmp_path):
        (tmp_path / "app").mkdir()
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "app" / "destination_filter.py").write_text("def apply(): pass\n")
        (tmp_path / "app" / "views.py").write_text("# the destination list is filtered here\n")
        (tmp_path / "app" / "unrelated.py").write_text("import os\n")
        (tmp_path / "node_modules" / "destination.js").write_text("destination filter\n")
        (tmp_path / "app" / "logo.png").write_bytes(b"\x89PNG\x00binary")
        return tmp_path

    def test_it_ranks_and_skips(self, tmp_path):
        hits = search_code(self.repo(tmp_path), "destination filter")
        paths = [h.path for h in hits]

        # The filename match wins: no amount of content matching recovers it.
        assert paths[0] == "app/destination_filter.py"
        assert "app/views.py" in paths
        # Vendored trees and binaries are never the answer.
        assert not any("node_modules" in p for p in paths)
        assert not any(p.endswith(".png") for p in paths)
        assert "app/unrelated.py" not in paths

    def test_a_missing_checkout_is_not_an_error(self, tmp_path):
        assert search_code(tmp_path / "nope", "anything") == []


class TestGather:
    def test_it_reports_the_files_past_changes_touched(self, tmp_path):
        # The one thing here no model can infer from a repository.
        details = [
            TicketDetail(
                ticket=make_ticket("#10", title="Etikettendruck Sammeldruck kaputt"),
                linked_changes=(
                    LinkedChange(ref="!5", title="fix", url="u", state="merged", merged=True),
                ),
            ),
            TicketDetail(
                ticket=make_ticket("#11", title="Etikettendruck Sammeldruck langsam"),
                linked_changes=(
                    LinkedChange(ref="!6", title="fix", url="u", state="merged", merged=True),
                ),
            ),
        ]
        tracker = FakeTracker(
            details,
            files={"!5": ["app/filters.py", "app/views.py"], "!6": ["app/filters.py"]},
        )
        context = gather("Etikettendruck Sammeldruck", tracker, "acme/shop", repo=tmp_path)

        # Ordered by how many related tickets touched each file.
        assert context.touched[0] == "app/filters.py"
        assert "app/views.py" in context.touched
        assert render_context(context).index("app/filters.py") < render_context(context).index(
            "Related tickets"
        )

    def test_an_unmerged_change_is_not_evidence(self, tmp_path):
        details = [
            TicketDetail(
                ticket=make_ticket("#12", title="Etikettendruck Sammeldruck kaputt"),
                linked_changes=(
                    LinkedChange(ref="!7", title="wip", url="u", state="opened", merged=False),
                ),
            )
        ]
        tracker = FakeTracker(details, files={"!7": ["app/abandoned.py"]})
        assert (
            gather("Etikettendruck Sammeldruck", tracker, "acme/shop", repo=tmp_path).touched == ()
        )

    def test_only_the_top_matches_cost_extra_requests(self, tmp_path):
        # Reading a merge request's files is an extra call each, and the
        # fifth-best match is rarely worth the wait.
        details = [
            make_detail(
                make_ticket(
                    f"#{i}", title="Etikettendruck Sammeldruck kaputt", description="Filter"
                ),
                merged=True,
            )
            for i in range(5)
        ]
        tracker = FakeTracker(details)
        gather("Etikettendruck Sammeldruck", tracker, "acme/shop", repo=tmp_path)
        assert tracker.file_calls == 3

    def test_a_new_subject_says_it_is_new(self, tmp_path):
        tracker = FakeTracker([make_detail(make_ticket("#20", title="Rechnung drucken"))])
        context = gather("Etikettendruck Anhang Upload", tracker, "acme/shop", repo=tmp_path)
        assert context.prior == ()
        assert "looks new" in render_context(context)

    def test_a_missing_checkout_still_returns_the_prior_art(self, tmp_path):
        # Half an answer is most of the value, so neither half aborts the run.
        tracker = FakeTracker(
            [make_detail(make_ticket("#21", title="Etikettendruck Sammeldruck kaputt"))]
        )
        context = gather("Etikettendruck Sammeldruck", tracker, "acme/shop", repo=tmp_path / "gone")
        assert context.prior
        assert context.files == ()
        assert any("not a directory" in note for note in context.notes)
