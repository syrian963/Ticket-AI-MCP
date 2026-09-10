# SPDX-License-Identifier: MIT

"""Picking a corpus when nobody supplied one.

The failure this file guards against is subtle and expensive: a corpus that
looks fine and quietly describes one bot, or one person, instead of the team.
"""

from __future__ import annotations

import pytest
from conftest import GOOD_BODY, make_detail, make_ticket

from ticket_ai_mcp.mining import pick, score


class TestExclusions:
    def test_bot_authors_are_dropped(self):
        details = [
            make_detail(make_ticket("#1", description=GOOD_BODY, author="renovate[bot]")),
            make_detail(make_ticket("#2", description=GOOD_BODY, author="mira")),
        ]
        taken, rejected = pick(details, want=10)
        assert [e.key for e in taken] == ["#2"]
        assert "bot" in rejected[0].reason

    def test_open_tickets_prove_nothing(self):
        details = [make_detail(make_ticket("#1", description=GOOD_BODY, state="open"))]
        taken, rejected = pick(details, want=10)
        assert taken == []
        assert "still open" in rejected[0].reason

    def test_a_stub_description_teaches_nothing(self):
        details = [make_detail(make_ticket("#1", description="broken"))]
        taken, rejected = pick(details, want=10)
        assert taken == []
        assert "characters" in rejected[0].reason


class TestScore:
    def test_a_structured_shipped_ticket_scores_high(self):
        detail = make_detail(make_ticket(description=GOOD_BODY, labels=("bug",)))
        assert score(detail).score > 0.85

    def test_a_shipped_ticket_beats_an_unshipped_one(self):
        shipped = score(make_detail(make_ticket(description=GOOD_BODY), merged=True))
        unshipped = score(make_detail(make_ticket(description=GOOD_BODY), merged=False))
        assert shipped.score > unshipped.score

    def test_clarifying_questions_cost_it(self):
        clear = score(make_detail(make_ticket(description=GOOD_BODY), questions=0))
        murky = score(make_detail(make_ticket(description=GOOD_BODY), questions=8))
        assert murky.score < clear.score
        assert any("not clear as written" in r for r in murky.reasons)

    def test_reopens_count_against_it(self):
        once = score(make_detail(make_ticket(description=GOOD_BODY), reopens=2))
        assert any("reopened 2x" in r for r in once.reasons)

    def test_a_ticket_closed_within_the_hour_is_discounted(self):
        quick = score(make_detail(make_ticket(description=GOOD_BODY, age_hours=0.2)))
        normal = score(make_detail(make_ticket(description=GOOD_BODY, age_hours=72)))
        assert quick.score < normal.score / 1.5

    def test_every_score_carries_its_reasons(self):
        # A corpus a team cannot argue with is a corpus they will not trust.
        assert score(make_detail(make_ticket(description=GOOD_BODY))).reasons


class TestDiversity:
    def test_one_author_cannot_own_the_corpus(self):
        # Mira wrote most of the board, and there are enough other authors
        # to fill the rest. Without the cap the profile would describe her.
        details = [
            make_detail(make_ticket(f"#{i}", description=GOOD_BODY, author="mira"))
            for i in range(20)
        ] + [
            make_detail(make_ticket(f"#5{i}", description=GOOD_BODY, author=f"dev{i}"))
            for i in range(12)
        ]
        taken, _ = pick(details, want=10, max_share_per_author=0.4)
        by_mira = sum(1 for e in taken if e.detail.ticket.author == "mira")
        assert len(taken) == 10
        assert by_mira <= 4

    def test_the_cap_yields_when_there_is_nobody_else(self):
        # Documented trade-off: a thin corpus is a worse problem than a
        # lopsided one, so the cap is a preference, not an invariant.
        details = [
            make_detail(make_ticket(f"#{i}", description=GOOD_BODY, author="mira"))
            for i in range(20)
        ] + [
            make_detail(make_ticket(f"#5{i}", description=GOOD_BODY, author=f"dev{i}"))
            for i in range(3)
        ]
        taken, _ = pick(details, want=10, max_share_per_author=0.4)
        assert len(taken) == 10
        assert sum(1 for e in taken if e.detail.ticket.author == "mira") == 7

    def test_a_thin_corpus_beats_a_balanced_empty_one(self):
        # If diversity leaves us short, take the skipped ones back. Ten
        # lopsided exemplars say more than four balanced ones.
        details = [
            make_detail(make_ticket(f"#{i}", description=GOOD_BODY, author="mira"))
            for i in range(20)
        ]
        taken, _ = pick(details, want=10, max_share_per_author=0.4)
        assert len(taken) == 10

    def test_the_ranking_does_not_get_to_choose_the_subject_matter(self):
        # The failure this guards against was found on a real board: bugs
        # attract clean merge requests, feature work does not, so ranking alone
        # returned a corpus of bugs and lost the template that stories follow.
        # Here half the pool is stories and none of them shipped an MR.
        bugs = [
            make_detail(
                make_ticket(f"#{i}", description=GOOD_BODY, labels=("Bug",), author=f"dev{i % 5}"),
                merged=True,
            )
            for i in range(20)
        ]
        stories = [
            make_detail(
                make_ticket(
                    f"#5{i}",
                    description=GOOD_BODY,
                    labels=("Ziel",),
                    author=f"po{i % 5}",
                ),
                merged=False,
                questions=3,
            )
            for i in range(20)
        ]
        taken, _ = pick(bugs + stories, want=10)
        stories_kept = sum(1 for e in taken if "Ziel" in e.detail.ticket.labels)
        assert len(taken) == 10
        # The pool is half stories, so the corpus should be too - give or take
        # the largest-remainder rounding.
        assert 4 <= stories_kept <= 6

    def test_stratifying_does_not_shrink_the_corpus(self):
        # Every kind gets a quota, and the quotas have to add up to `want`
        # exactly rather than drifting down through rounding.
        details = [
            make_detail(
                make_ticket(
                    f"#{i}", description=GOOD_BODY, labels=(f"kind{i % 7}",), author=f"d{i}"
                )
            )
            for i in range(40)
        ]
        for want in (5, 9, 13, 30):
            taken, _ = pick(details, want=want)
            assert len(taken) == want

    def test_results_are_ordered_and_stable(self):
        details = [
            make_detail(make_ticket(f"#{i}", description=GOOD_BODY, author=f"dev{i}"))
            for i in range(6)
        ]
        first, _ = pick(details, want=5)
        second, _ = pick(list(reversed(details)), want=5)
        assert [e.key for e in first] == [e.key for e in second]


class TestEveryReadFailing:
    """A corpus where the listing worked and nothing behind it did.

    Found anonymously on a public GitLab board: forty tickets listed, and the
    history of every one came back 401. The tool profiled the empty result and
    reported a page of zeroes - a label rate of 0.00 and no sections reads
    exactly like a team that labels nothing and writes no headings.
    """

    def tracker(self, count: int):
        from ticket_ai_mcp.trackers import TrackerError

        class Refusing:
            name = "test"

            def search(self, query):
                return [make_ticket(f"#{i}", description=GOOD_BODY) for i in range(count)]

            def fetch(self, project, key):  # pragma: no cover - unused here
                raise TrackerError("no")

            def detail(self, ticket):
                raise TrackerError("401 Unauthorized")

        return Refusing()

    def test_it_refuses_instead_of_profiling_nothing(self):
        from ticket_ai_mcp.corpus import by_mining
        from ticket_ai_mcp.trackers import TrackerError

        with pytest.raises(TrackerError, match="none of them could be read"):
            by_mining(self.tracker(40), "acme/shop", sample=40, want=10)

    def test_a_project_with_no_closed_tickets_is_not_an_error(self):
        # Nothing listed is a quiet project, which is a fact about the board
        # rather than a failure, and it still has to come back empty.
        from ticket_ai_mcp.corpus import by_mining

        gathered = by_mining(self.tracker(0), "acme/shop", sample=40, want=10)
        assert gathered.exemplars == []
        assert not gathered.enough
