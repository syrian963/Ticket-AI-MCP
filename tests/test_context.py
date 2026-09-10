# SPDX-License-Identifier: MIT

"""Gathering the evidence for a ticket that has not been written yet.

The thing being protected here is the division of labour. This half is
deterministic - the same subject gives the same files and the same prior art,
every time, with no model and no key - and the moment it starts guessing at
meaning it stops being checkable.
"""

from __future__ import annotations

from conftest import make_detail, make_ticket

from ticket_ai_mcp.codebase import _is_changelog
from ticket_ai_mcp.codebase import _names_it as names_it
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


class TestTheFileNamedAfterTheSubject:
    """A plural in the filename should not cost it the name bonus.

    Found against a real checkout of Flask. Asked about a "session cookie",
    the search scored `src/flask/sessions.py` at 4.0 - the same as
    `docs/templating.rst` - because the subject says `session` and the file
    says `sessions`, so the name match never fired and the tie broke
    alphabetically. The one file in the repository named after the subject was
    not in the top ten.
    """

    def tree(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "docs").mkdir()
        (tmp_path / "src/sessions.py").write_text(
            "class SessionInterface:\n    def open_session(self):\n        ...\n",
            encoding="utf-8",
        )
        (tmp_path / "src/blueprints.py").write_text("class Blueprint:\n    ...\n", encoding="utf-8")
        # A document that mentions the words and is about none of them, which
        # is what a changelog or a tutorial is.
        (tmp_path / "docs/templating.rst").write_text(
            "The session and the cookie and the secure flag are all discussed here.\n",
            encoding="utf-8",
        )
        return tmp_path

    def test_the_singular_subject_finds_the_plural_file(self, tmp_path):
        hits = search_code(self.tree(tmp_path), "session cookie is not set when secure is on")
        assert hits[0].path == "src/sessions.py"
        assert "session" in hits[0].in_name

    def test_a_plural_subject_finds_the_singular_file(self, tmp_path):
        # Both directions, because a title is as likely to say "blueprints".
        hits = search_code(self.tree(tmp_path), "blueprints ignore the url prefix")
        assert hits[0].path == "src/blueprints.py"

    def test_a_short_word_does_not_get_the_benefit(self, tmp_path):
        # Four characters of stem, or `bus` matches `bu` and `class` matches
        # `clas`. This is one exception for plurals, not a stemmer.
        assert names_it("session", {"sessions"}) is True
        assert names_it("sessions", {"session"}) is True
        assert names_it("bus", {"bu"}) is False
        assert names_it("abc", {"abcs"}) is False


class TestAChangelogIsNotAnAnswer:
    """A record of every change mentions every word the project has used.

    Measured over six unrelated subjects on two real checkouts: `CHANGES.rst`
    came back for five of them and `CHANGELOG.md` for five - not because it
    was relevant five times, but because it is a concatenation of every
    subject the project has ever had, and it was crowding out the file the
    work lands in.
    """

    def tree(self, tmp_path):
        """A source file the subject does not name, and a changelog that does.

        Written this way on purpose. The first version of this test gave the
        source file a matching *name*, which is worth four times a body match
        - so it won with the fix and without it, and proved nothing. The case
        that needs the weighting is the one where both files match the same
        words in their contents and the changelog wins the tie on its path.
        """
        (tmp_path / "src").mkdir()
        (tmp_path / "src/handler.py").write_text(
            "# the parser drops a trailing slash here\n", encoding="utf-8"
        )
        (tmp_path / "CHANGELOG.md").write_text(
            "- the parser drops a trailing slash\n- unrelated cookie fix\n", encoding="utf-8"
        )
        return tmp_path

    def test_the_source_file_wins(self, tmp_path):
        hits = search_code(self.tree(tmp_path), "the parser drops a trailing slash")
        assert hits[0].path == "src/handler.py"

    def test_but_it_is_not_hidden(self, tmp_path):
        # Halved, not excluded: "the changelog is missing an entry" is a real
        # ticket and this is the file it is about.
        paths = [h.path for h in search_code(self.tree(tmp_path), "changelog entry for the parser")]
        assert "CHANGELOG.md" in paths

    def test_the_names_it_recognises(self, tmp_path):
        assert _is_changelog("CHANGES.rst")
        assert _is_changelog("docs/CHANGELOG.md")
        assert _is_changelog("HISTORY.txt")
        assert _is_changelog("whats-new.md")
        assert not _is_changelog("src/changes_view.py")
        assert not _is_changelog("src/newsletter.py")


class TestLengthIsNotRelevance:
    """A long ticket contains every word by accident, not by aboutness.

    `rank` used to divide by the subject's weight and nothing else, on the
    argument that a long ticket containing every word is a better match rather
    than a worse one. Measured over six unrelated subjects against 120 closed
    tickets from home-assistant/core, the top match came back **longer than
    83% of the pool on average**, and longer than 88% for four of the six.
    With the length term it is 56% - about what you would expect if length
    were not deciding.

    The assertion below is deliberately the narrow one. "The shorter ticket
    wins" is a preference this project has not earned; "the same handful of
    matched words is worth less inside forty paragraphs than inside two" is
    exactly what the length term does and all it claims.
    """

    SHARED = "Etikettendruck bricht beim Sammeldruck ab"

    def pool(self):
        # Identical overlap with the subject, identical titles' irrelevance,
        # forty times the padding. Nothing but length is different, so nothing
        # but length can explain a difference in score.
        padding = (
            " Wareneingang Inventur Kommissionierung Retoure Versand Nachschub "
            "Umlagerung Bestandskorrektur Lagerplatz Charge"
        ) * 20
        return [
            make_ticket("#1", title="Lager", description=self.SHARED),
            make_ticket("#2", title="Lager", description=self.SHARED + padding),
        ]

    def test_the_same_words_are_worth_less_in_a_longer_ticket(self):
        scores = {m.ticket.key: m.score for m in rank(self.SHARED, self.pool(), limit=2)}
        assert scores["#1"] > scores.get("#2", 0.0)

    def test_a_long_ticket_is_still_findable_when_it_is_the_answer(self):
        # Discounted, not excluded: a subject that only the padded ticket
        # covers still has to reach it.
        keys = [m.ticket.key for m in rank("Inventur Kommissionierung Retoure", self.pool())]
        assert keys[0] == "#2"


class TestTheConnectiveTissueOfEveryBugReport:
    def test_a_shared_after_is_not_a_relationship(self):
        # Found on home-assistant/core: a ticket about a to-do trigger came
        # back as the best match for a subject about an MQTT sensor, and the
        # words they shared were "after" and its neighbours. Those are in
        # every bug report ever written, which is the same reason "when",
        # "then" and "should" were already dropped.
        for word in ("after", "before", "again", "still", "always", "during", "while"):
            assert word not in tokens(f"it broke {word} the restart")
        # And the words that carry the subject survive.
        assert "restart" in tokens("it broke after the restart")
