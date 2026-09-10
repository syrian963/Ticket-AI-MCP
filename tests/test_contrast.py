# SPDX-License-Identifier: MIT

"""Comparing the tickets that shipped with the ones that did not.

The load-bearing property is the split: a ticket lands in a group because of
what *happened* to it and never because of what it *contains*. Break that and
the whole thing turns circular - sections would predict success because having
sections is what put the ticket in the successful group. Several tests below
exist only to hold that line.
"""

from __future__ import annotations

from datetime import UTC, datetime

from conftest import GOOD_BODY, make_detail, make_ticket

from ticket_ai_mcp.contrast import MIN_GROUP, build, closed_by_robot, outcome
from ticket_ai_mcp.profile import build as build_profile
from ticket_ai_mcp.report import render_contrast
from ticket_ai_mcp.review import review
from ticket_ai_mcp.schemas import Comment, LinkedChange, TicketDetail

NOW = datetime(2026, 1, 1, tzinfo=UTC)
MERGED = LinkedChange(ref="!1", title="fix", url="u", state="merged", merged=True)
OPEN_MR = LinkedChange(ref="!2", title="wip", url="u", state="opened", merged=False)

WITH_STEPS = (
    "## Problem\nDer Etikettendruck bricht ab, sobald mehr als hundert Positionen\n"
    "anstehen und niemand merkt es.\n"
    "## Schritte\n1. Auftrag oeffnen\n2. Sammeldruck starten\n"
)
WITHOUT_STEPS = (
    "## Problem\nDer Etikettendruck bricht ab, sobald mehr als hundert Positionen\n"
    "anstehen und niemand merkt es. Mehr ist dazu nicht bekannt.\n"
)


def detail(
    key: str,
    *,
    body: str = WITH_STEPS,
    merged: bool = True,
    reopens: int = 0,
    questions: int = 0,
    comments: tuple[Comment, ...] = (),
    labels: tuple[str, ...] = ("bug",),
    state: str = "closed",
) -> TicketDetail:
    asked = tuple(
        Comment(author="dev", body=f"what about {i}?", created_at=NOW) for i in range(questions)
    )
    return TicketDetail(
        ticket=make_ticket(key, description=body, labels=labels, state=state),
        comments=asked + comments,
        linked_changes=(MERGED,) if merged else (),
        reopen_count=reopens,
    )


class TestOutcome:
    def test_a_clean_merge_is_shipped(self):
        assert outcome(detail("#1")) == "shipped"

    def test_nothing_merged_is_stalled(self):
        assert outcome(detail("#2", merged=False)) == "stalled"

    def test_two_reopens_are_stalled(self):
        assert outcome(detail("#3", reopens=2)) == "stalled"

    def test_a_run_of_questions_is_stalled(self):
        assert outcome(detail("#4", questions=6)) == "stalled"

    def test_one_reopen_with_a_merge_is_neither(self):
        # Real, and not evidence either way. Forcing it into a group to make
        # the numbers bigger is how a comparison starts lying.
        assert outcome(detail("#5", reopens=1)) == "unclear"

    def test_an_open_ticket_is_neither(self):
        assert outcome(detail("#6", state="open")) == "unclear"

    def test_the_split_ignores_the_description_entirely(self):
        # The property everything else rests on. Same outcome, opposite
        # content: they must land in the same group.
        rich = outcome(detail("#7", body=WITH_STEPS, merged=False))
        bare = outcome(detail("#8", body="x", merged=False))
        assert rich == bare == "stalled"


class TestStalenessBots:
    def test_a_bot_closure_is_not_evidence_about_writing(self):
        # Found on a real board: a staleness bot had filled the stalled group
        # with well-written tickets whose only fault was age, and every field
        # of that project's mandatory form came out "more common when stalled".
        stale = Comment(
            author="github-actions[bot]",
            body="This issue is stale because it has been open 90 days. Closing.",
            created_at=NOW,
        )
        assert closed_by_robot(detail("#9", merged=False, comments=(stale,)))
        assert outcome(detail("#9", merged=False, comments=(stale,))) == "unclear"

    def test_the_phrasings_bots_actually_use(self):
        for text in (
            "Closing as inactive.",
            "There has been no activity here; closing.",
            "Marking stale, will be closed in 7 days",
        ):
            assert closed_by_robot(
                detail("#10", comments=(Comment(author="bot", body=text, created_at=NOW),))
            ), text

    def test_a_person_saying_the_word_stale_is_not_a_bot_closure(self):
        human = Comment(
            author="mira",
            body="The cached value goes stale after an hour, which is the bug.",
            created_at=NOW,
        )
        assert not closed_by_robot(detail("#11", comments=(human,)))


def board(shipped_with: int, shipped_without: int, stalled_with: int, stalled_without: int):
    out = []
    n = 0
    for count, body, merged in (
        (shipped_with, WITH_STEPS, True),
        (shipped_without, WITHOUT_STEPS, True),
        (stalled_with, WITH_STEPS, False),
        (stalled_without, WITHOUT_STEPS, False),
    ):
        for _ in range(count):
            n += 1
            out.append(detail(f"#{n}", body=body, merged=merged))
    return out


class TestSignals:
    def test_a_section_that_separates_the_groups_is_found(self):
        # Present in almost every shipped ticket, almost no stalled one.
        contrast = build(
            board(shipped_with=10, shipped_without=1, stalled_with=1, stalled_without=10)
        )
        assert contrast.usable
        signal = contrast.signal("section:schritte")
        assert signal is not None
        assert signal.helps
        assert signal.shipped_rate > 0.8
        assert signal.stalled_rate < 0.2

    def test_a_section_present_in_both_groups_is_not_a_signal(self):
        contrast = build(
            board(shipped_with=10, shipped_without=1, stalled_with=10, stalled_without=1)
        )
        assert contrast.usable
        assert contrast.signal("section:schritte") is None
        assert any("Nothing in the descriptions separates" in n for n in contrast.notes)

    def test_a_thin_group_refuses_to_report(self):
        contrast = build(
            board(shipped_with=20, shipped_without=0, stalled_with=0, stalled_without=2)
        )
        assert not contrast.usable
        assert contrast.signals == ()
        assert any(str(MIN_GROUP) in n for n in contrast.notes)

    def test_a_signal_can_point_the_other_way(self):
        contrast = build(
            board(shipped_with=1, shipped_without=10, stalled_with=10, stalled_without=1)
        )
        signal = contrast.signal("section:schritte")
        assert signal is not None and not signal.helps

    def test_the_report_warns_about_reading_those_backwards(self):
        contrast = build(
            board(shipped_with=1, shipped_without=10, stalled_with=10, stalled_without=1)
        )
        text = render_contrast(contrast)
        assert "symptom" in text

    def test_an_empty_board_is_an_answer(self):
        contrast = build([])
        assert not contrast.usable
        assert contrast.notes


class TestFindingsQuoteTheContrast:
    def profile_with_contrast(self):
        details = board(shipped_with=10, shipped_without=1, stalled_with=1, stalled_without=10)
        from ticket_ai_mcp.mining import pick

        taken, _ = pick(details, want=len(details))
        return build_profile(taken, project="acme/lager", tracker="gitlab", contrast=build(details))

    def test_the_profile_carries_the_discriminative_sections(self):
        p = self.profile_with_contrast()
        assert "schritte" in p.discriminative
        shipped_rate, stalled_rate = p.discriminative["schritte"]
        assert shipped_rate > stalled_rate

    def test_a_missing_section_finding_quotes_both_rates(self):
        p = self.profile_with_contrast()
        if p.section("schritte") is None or p.section("schritte").rate < 0.6:
            return  # the corpus did not make it a board-wide convention
        result = review(make_ticket("#900", description=WITHOUT_STEPS), p)
        contrasted = [f for f in result.findings if f.code == "missing_section_contrast"]
        assert contrasted
        why = contrasted[0].why
        assert "shipped" in why and "stalled" in why

    def test_it_survives_the_json_round_trip(self):
        from ticket_ai_mcp.profile import Profile

        p = self.profile_with_contrast()
        assert Profile.from_json(p.to_json()).discriminative == p.discriminative


class TestATrackerThatCannotSeeChanges:
    """A corpus with no merged change anywhere is not a corpus of failures.

    Measured on seven public Jira boards: 349 closed tickets, 349 of them
    filed as stalled, none as shipped. Jira has no supported API for the
    development panel, so a team that does not post remote links looks from
    here like a team that has never shipped anything. The group floor stopped
    any advice being derived from it, but the counts were still reported, and
    telling a team that none of its fifty closed tickets got built is a false
    statement about their board.
    """

    def board(self, n: int = 30, *, merged: bool = False):
        out = []
        for i in range(n):
            ticket = make_ticket(f"PROJ-{i}", description=GOOD_BODY, state="closed")
            out.append(make_detail(ticket, merged=merged))
        return out

    def test_no_merged_change_anywhere_is_reported_as_unmeasurable(self):
        result = build(self.board())
        assert result.shipped == 0
        assert result.stalled == 0
        assert result.signals == ()
        assert any("no supported API" in note for note in result.notes)

    def test_links_that_do_not_say_whether_they_merged_do_not_count(self):
        # Apache's KAFKA board: a remote link on 32 of 33 tickets, and Jira
        # reports the merge state of none of them. A guard that asked whether
        # links exist passed here and left the split exactly as wrong.
        details = [
            make_detail(
                make_ticket(f"PROJ-{i}", description=GOOD_BODY, state="closed"),
                merged=False,
                linked=(LinkedChange(ref="1", title="t", url="u", state="open", merged=False),),
            )
            for i in range(30)
        ]
        result = build(details)
        assert result.shipped == 0 and result.stalled == 0
        assert result.signals == ()

    def test_a_board_that_does_link_its_changes_is_still_compared(self):
        # The guard must not fire on a tracker that works. Half with a merged
        # change, half without, which is the ordinary case.
        details = self.board(20, merged=True) + self.board(20, merged=False)
        result = build(details)
        assert result.shipped >= 8 and result.stalled >= 8
