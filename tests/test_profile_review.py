# SPDX-License-Identifier: MIT

"""The claim the whole tool rests on.

Not "your ticket should have acceptance criteria" - anyone can say that - but
"31 of your last 40 shipped tickets have one, and this does not". These tests
exist to keep the second sentence true: if a finding ever stops citing a real
count over a real sample, the tool is back to giving generic advice.
"""

from __future__ import annotations

import json
import re
import time

from conftest import GOOD_BODY, make_detail, make_ticket

from ticket_ai_mcp.mining import pick, score
from ticket_ai_mcp.profile import MAX_TRIGGERS, Profile, build, clusters
from ticket_ai_mcp.report import render_profile, render_review
from ticket_ai_mcp.review import review, review_draft


def corpus(n: int = 20, *, body: str = GOOD_BODY, labels=("bug", "team::shop")) -> Profile:
    details = [
        make_detail(make_ticket(f"#{i}", description=body, labels=labels, author=f"dev{i % 7}"))
        for i in range(n)
    ]
    taken, _ = pick(details, want=n)
    return build(taken, project="acme/shop", tracker="gitlab")


class TestProfile:
    def test_it_finds_the_template(self):
        p = corpus()
        keys = {s.key for s in p.sections}
        assert {"problem", "steps to reproduce", "acceptance criteria"} <= keys
        assert p.section("acceptance criteria").rate == 1.0

    def test_a_heading_used_once_is_not_a_convention(self):
        details = [
            make_detail(make_ticket(f"#{i}", description=GOOD_BODY, author=f"dev{i}"))
            for i in range(9)
        ]
        details.append(
            make_detail(make_ticket("#99", description=GOOD_BODY + "\n## Nur Einmal\nx" * 20))
        )
        taken, _ = pick(details, want=10)
        p = build(taken, project="acme/shop", tracker="gitlab")
        assert p.section("nur einmal") is None

    def test_scoped_label_groups_are_detected(self):
        assert corpus().label_groups == ("team",)

    def test_a_thin_sample_says_so(self):
        p = corpus(n=4)
        assert p.is_thin
        assert any("4 tickets" in note for note in p.notes)

    def test_an_empty_corpus_is_an_answer_not_a_crash(self):
        p = build([], project="acme/shop", tracker="gitlab")
        assert p.sample_size == 0
        assert p.notes

    def test_it_survives_a_round_trip_through_json(self):
        # The profile is cached on disk between runs; a lossy round trip would
        # silently change every rate the next morning.
        p = corpus()
        again = Profile.from_json(p.to_json())
        assert again == p

    def test_language_is_taken_from_the_corpus(self):
        german = (
            "## Problem\nWenn der Lagerist auf Drucken klickt, wird die letzte "
            "Position nicht gedruckt und das ist nicht richtig so.\n"
            "## Akzeptanzkriterien\n- [ ] Jede Position bekommt ein Etikett\n"
        )
        assert corpus(body=german).language == "de"


STORY = (
    "## Ziel\nAls Lagerist moechte ich Etiketten drucken koennen, damit die Ware zugeordnet ist.\n"
)
ABNAHME = "## Abnahme\n- [ ] Jede Position bekommt ein Etikett\n- [ ] Ein Test deckt den Rand ab\n"
BUG = (
    "## Problem\nDer Etikettendruck bricht ab, sobald mehr als hundert Positionen anstehen.\n"
    "Betroffen ist nur der Sammeldruck; einzelne Etiketten werden weiterhin\n"
    "korrekt erzeugt, sodass es im Alltag lange unbemerkt bleibt.\n"
)


def split_board(stories: int = 12, bugs: int = 18, ak_on_bugs: int = 3):
    """A board with two ticket shapes, the way a real one looks.

    Stories carry Ziel + ABNAHME. Bugs mostly do not. Board-wide the Abnahme rate
    lands well under any usable threshold, which is exactly the case that made
    conditional conventions necessary.
    """
    details = []
    for i in range(stories):
        details.append(
            make_detail(make_ticket(f"#{i}", description=STORY + ABNAHME, author=f"po{i % 4}"))
        )
    for i in range(bugs):
        body = BUG + (ABNAHME if i < ak_on_bugs else "")
        details.append(make_detail(make_ticket(f"#5{i}", description=body, author=f"dev{i % 5}")))
    taken, _ = pick(details, want=stories + bugs)
    return build(taken, project="acme/shop", tracker="gitlab")


class TestConditionalConventions:
    def test_a_convention_the_board_wide_rate_hides(self):
        p = split_board()
        overall = p.section("abnahme")
        # Under any threshold worth having, so the plain section check is off.
        assert overall.rate < 0.6

        rule = next(c for c in p.conditionals if c.when == "ziel" and c.then == "abnahme")
        assert rule.rate == 1.0
        assert rule.baseline < 0.6
        assert rule.lift > 0.25

    def test_it_fires_only_on_the_tickets_it_applies_to(self):
        p = split_board()
        story = make_ticket("#600", description=STORY + "\nSome more detail about it here.\n")
        codes = [f.code for f in review(story, p).findings]
        assert "missing_conditional_section" in codes

        # A bug ticket has no Ziel section, so the rule never applied and
        # must not be held against it.
        bug = make_ticket("#601", description=BUG + "\nSome more detail about it here.\n")
        assert "missing_conditional_section" not in [f.code for f in review(bug, p).findings]

    def test_a_satisfied_pair_is_reported_once(self):
        # Both directions of a pair are real checks and both are scored, but
        # "has Abnahme to go with Ziel" next to "has Ziel to go with
        # Abnahme" reads as a stutter.
        p = split_board()
        result = review(make_ticket("#603", description=STORY + ABNAHME), p)
        pairs = [line for line in result.passed if "to go with" in line]
        assert len(pairs) == 1
        # Still two checks, so the score is unaffected.
        assert result.checks_run >= 3

    def test_the_skeleton_includes_a_template_that_lives_in_a_block(self):
        # The bug the UI exposed. On a real board Abnahme sat at 37% and Ziel
        # at 27%, so a skeleton built from board-wide rates was empty and the
        # tool announced that the team writes prose. They do not: those two go
        # together on nine of the eleven tickets that have either.
        p = split_board()
        by_rate = {s.heading for s in p.sections if s.rate >= 0.6}
        assert "Abnahme" not in by_rate and "Ziel" not in by_rate

        skeleton = dict(p.skeleton())
        assert "Abnahme" in skeleton and "Ziel" in skeleton
        assert "goes with" in skeleton["Abnahme"]
        assert "goes with" in skeleton["Ziel"]

    def test_the_skeleton_reads_in_the_order_tickets_are_written(self):
        # Found with a live model. `Abnahme` is more common on this board than
        # `Ziel`, so a skeleton sorted by frequency listed it first -
        # and llama3.2 obediently wrote the summary under `Abnahme` and the
        # criteria under `Ziel`. Tickets there run the other way.
        p = split_board()
        headings = [h for h, _ in p.skeleton()]
        assert headings.index("Ziel") < headings.index("Abnahme")

        # The section that is more common is still the more common one; only
        # the reading order changed.
        assert p.section("abnahme").count > p.section("ziel").count

    def test_position_is_measured_from_where_headings_actually_appear(self):
        p = split_board()
        assert p.section("ziel").position < p.section("abnahme").position

    def test_the_skeleton_says_which_claim_each_heading_rests_on(self):
        # "in 78% of tickets" and "goes with Ziel on 9 of 11" are
        # different claims and a writer should see which one applies.
        p = corpus()
        why = dict(p.skeleton())
        assert why["Acceptance criteria"].startswith("in ")

    def test_a_board_with_no_template_gets_an_empty_skeleton(self):
        varied = [
            make_detail(
                make_ticket(
                    f"#{i}",
                    description=f"## Heading{i}\n" + "Prose about the thing. " * 30,
                    author=f"dev{i}",
                )
            )
            for i in range(15)
        ]
        taken, _ = pick(varied, want=15)
        assert build(taken, project="a", tracker="gitlab").skeleton() == ()

    def test_the_finding_quotes_both_numbers(self):
        # The conditional rate alone proves nothing - it is the gap against the
        # baseline that makes it a rule rather than a smaller window.
        p = split_board()
        story = make_ticket("#602", description=STORY + "\nSome more detail about it here.\n")
        why = next(
            f.why for f in review(story, p).findings if f.code == "missing_conditional_section"
        )
        assert "against" in why
        assert re.search(r"\d+ of the \d+ exemplar tickets", why)

    def test_coincidence_is_not_a_convention(self):
        # Where Abnahme is just as common with or without the story section, there is
        # no rule to state and none should be invented.
        details = [
            make_detail(
                make_ticket(
                    f"#{i}",
                    description=(STORY if i % 2 else BUG) + ABNAHME,
                    author=f"dev{i % 6}",
                )
            )
            for i in range(20)
        ]
        taken, _ = pick(details, want=20)
        p = build(taken, project="acme/shop", tracker="gitlab")
        assert [c for c in p.conditionals if c.then == "abnahme"] == []

    def test_the_bar_rises_with_the_number_of_pairs_tested(self):
        # Every ordered pair of sections is a hypothesis. Measured against
        # random data, a pair with no association clears a flat 0.25 lift 8%
        # of the time - about fourteen invented rules on a board with fourteen
        # sections, which is why sixteen of thirty real boards came back
        # holding exactly the twelve the display cap allowed.
        from ticket_ai_mcp.profile import MIN_LIFT, required_lift

        assert required_lift(2) == MIN_LIFT
        assert required_lift(6) == MIN_LIFT
        assert required_lift(180) > MIN_LIFT
        # Monotonic, and capped so a very wide board is not unanswerable.
        assert required_lift(12) < required_lift(48) < required_lift(180)
        assert required_lift(10_000) <= 0.55

    def test_a_wide_board_does_not_fill_up_with_coincidences(self):
        # Sixteen sections scattered at random across thirty tickets: 240
        # ordered pairs and nothing real between any of them. Averaged over
        # several boards, because a single seed proves nothing either way -
        # the first version of this test passed with the flat threshold too,
        # which meant it was testing nothing.
        import random

        # Low prevalence is the point. A section in five of thirty tickets
        # passes the old trigger and its conditional rate swings twenty points
        # on one ticket, which is exactly where invented rules come from - and
        # why sections carried by half the board never produced them.
        totals = []
        for seed in range(6):
            random.seed(seed)
            details = []
            for i in range(30):
                chosen = [f"## Abschnitt {j}\n" for j in range(16) if random.random() < 0.2]
                body = "".join(chosen) + ("Fliesstext ueber das Problem. " * 20)
                details.append(
                    make_detail(make_ticket(f"#{i}", description=body, author=f"dev{i % 6}"))
                )
            taken, _ = pick(details, want=30)
            p = build(taken, project="acme/wide", tracker="gitlab")
            totals.append(len([c for c in p.conditionals if c.rate >= 0.6]))

        average = sum(totals) / len(totals)
        # Under the flat threshold this runs into double figures. Sixteen of
        # thirty real boards came back holding exactly the twelve the display
        # cap allowed, which is what a cap doing the selecting looks like.
        assert average <= 3, f"{average:.1f} coincidences per board on random data: {totals}"

    def test_a_rare_trigger_is_not_a_rule(self):
        # Two tickets agreeing with each other is a coincidence with a percent
        # sign on it.
        p = split_board(stories=2, bugs=20, ak_on_bugs=0)
        assert [c for c in p.conditionals if c.when == "ziel"] == []

    def test_a_block_of_sections_reads_as_one_rule_not_six(self):
        # A bug form's version / OS / Python fields imply each other in both
        # directions, which is six pairwise rules and one fact. Six lines
        # saying the same thing is how a report stops being read.
        form = (
            "## FastAPI Version\n0.115\n## Operating System\nLinux\n"
            "## Python Version\n3.12\nUnd noch etwas Fliesstext dazu, damit die "
            "Beschreibung lang genug ist um gezaehlt zu werden.\n"
        )
        details = [
            make_detail(make_ticket(f"#{i}", description=form, author=f"dev{i % 5}"))
            for i in range(10)
        ] + [
            make_detail(make_ticket(f"#5{i}", description=BUG, author=f"dev{i % 5}"))
            for i in range(20)
        ]
        taken, _ = pick(details, want=30)
        p = build(taken, project="acme/shop", tracker="gitlab")

        blocks = clusters(p.conditionals)
        assert len(blocks) == 1
        assert set(blocks[0]) == {"FastAPI Version", "Operating System", "Python Version"}

        rendered = render_profile(p)
        assert rendered.count("appear as a block") == 1

    def test_a_one_way_rule_is_not_folded_into_a_block(self):
        # "Everything with a ziel has acceptance criteria" does not mean
        # everything with acceptance criteria has a ziel.
        p = split_board(stories=12, bugs=18, ak_on_bugs=9)
        one_way = [c for c in p.conditionals if c.when == "ziel" and c.then == "abnahme"]
        assert one_way and one_way[0].rate == 1.0
        assert clusters(p.conditionals) == []

    def test_conditionals_survive_the_json_round_trip(self):
        p = split_board()
        assert Profile.from_json(p.to_json()) == p


class TestDraft:
    """Checking a ticket before it exists, which is the only useful time."""

    def test_a_draft_is_measured_exactly_like_a_created_ticket(self):
        # A separate code path for drafts would drift, and then a draft would
        # pass what the created ticket fails.
        p = corpus()
        created = review(
            make_ticket("#700", description=GOOD_BODY, labels=("bug", "team::shop")), p
        )
        drafted = review_draft("Fix the label printing", GOOD_BODY, p, labels=("bug", "team::shop"))
        assert [f.code for f in drafted.findings] == [f.code for f in created.findings]
        assert drafted.alignment == created.alignment

    def test_a_thin_draft_is_caught_before_it_reaches_the_board(self):
        p = corpus()
        result = review_draft("Etikettendruck kaputt", "Geht nicht.", p)
        assert result.ticket_key == "(draft)"
        assert any(f.code == "missing_section" for f in result.findings)

    def test_labels_it_will_carry_are_taken_into_account(self):
        p = corpus()
        without = review_draft("t", GOOD_BODY, p)
        with_labels = review_draft("t", GOOD_BODY, p, labels=("bug", "team::shop"))
        assert any(f.code == "no_labels" for f in without.findings)
        assert not any(f.code == "no_labels" for f in with_labels.findings)


class TestReview:
    def test_a_missing_section_cites_the_count(self):
        p = corpus()
        thin = make_ticket(
            "#500", description="Export is broken, please fix it soon.", state="open"
        )
        result = review(thin, p)
        missing = [f for f in result.findings if f.code == "missing_section"]
        assert missing
        # The number in `why` has to be a real count over the real sample.
        for finding in missing:
            match = re.search(r"(\d+) of the (\d+) exemplar", finding.why)
            assert match, finding.why
            assert int(match.group(1)) <= int(match.group(2)) == p.sample_size

    def test_a_conforming_ticket_is_left_alone(self):
        p = corpus()
        good = make_ticket("#501", description=GOOD_BODY, labels=("bug", "team::shop"))
        result = review(good, p)
        assert [f.code for f in result.findings] == []
        assert result.alignment == 1.0

    def test_an_empty_description_stops_there(self):
        # Every later check measures a description. Reporting nine findings
        # about a ticket with no text is noise around the one that matters.
        p = corpus()
        result = review(make_ticket("#502", description=""), p)
        assert [f.code for f in result.findings] == ["empty_description"]
        assert result.alignment == 0.0

    def test_a_short_description_is_measured_against_the_corpus(self):
        p = corpus()
        short = make_ticket("#503", description=GOOD_BODY[:150])
        finding = next(f for f in review(short, p).findings if f.code == "short_description")
        assert str(round(p.chars_p25)) in finding.why

    def test_a_missing_scoped_label_is_flagged(self):
        p = corpus()
        result = review(make_ticket("#504", description=GOOD_BODY, labels=("bug",)), p)
        assert any(f.code == "missing_label_group" for f in result.findings)

    def test_language_mismatch(self):
        german = (
            "## Problem\nWenn der Lagerist auf Drucken klickt, wird die letzte "
            "Position nicht gedruckt und das ist nicht richtig so.\n"
            "## Akzeptanzkriterien\n- [ ] Jede Position bekommt ein Etikett\n"
        )
        p = corpus(body=german)
        english = make_ticket(
            "#505",
            description=(
                "## Problem\nWhen the user clicks export the last row is not "
                "written and that is not what should happen here.\n"
                "## Akzeptanzkriterien\n- [ ] every row is there\n"
            ),
        )
        assert any(f.code == "language_mismatch" for f in review(english, p).findings)

    def test_no_profile_means_no_findings(self):
        # Findings without a corpus would be the tool's taste wearing the
        # corpus's authority. Say there is nothing to compare against instead.
        empty = build([], project="acme/shop", tracker="gitlab")
        result = review(make_ticket("#506", description="anything"), empty)
        assert result.findings == ()
        assert result.caveats

    def test_a_thin_profiles_caveat_reaches_the_review(self):
        p = corpus(n=4)
        result = review(make_ticket("#507", description="short", state="open"), p)
        assert any("4 tickets" in c for c in result.caveats)

    def test_a_board_with_no_conventions_measures_nothing_and_says_so(self):
        # Found on a real project: 35 of 40 open tickets read "100%, nothing to
        # flag" because the corpus had no convention strong enough to enable a
        # single check. Silence is not a pass.
        varied = [
            make_detail(
                make_ticket(
                    f"#{i}",
                    description=f"## Heading{i}\n" + "Prose about the thing. " * 30,
                    labels=(),
                    assignees=(),
                    author=f"dev{i}",
                )
            )
            for i in range(15)
        ]
        taken, _ = pick(varied, want=15)
        p = build(taken, project="acme/shop", tracker="gitlab")
        assert [s for s in p.sections if s.rate >= 0.6] == []

        # Only the length check survives a corpus this varied. The score is
        # still a number, but the report has to carry how thin it is - a bare
        # "100%" here would be the exact lie the old arithmetic told.
        result = review(make_ticket("#600", description="prose " * 60), p)
        assert result.checks_run == 1
        assert "over 1 checks" in render_review(result)

    def test_an_empty_profile_scores_nothing_rather_than_everything(self):
        empty = build([], project="acme/shop", tracker="gitlab")
        result = review(make_ticket("#603", description="anything"), empty)
        assert result.alignment is None
        assert "not measurable" in render_review(result)

    def test_alignment_is_a_share_of_the_checks_that_applied(self):
        p = corpus()
        # Right template and length, wrong labels: some checks pass, some fail,
        # so the score has to land strictly between the two extremes.
        partly = make_ticket("#601", description=GOOD_BODY, labels=())
        result = review(partly, p)
        assert result.findings
        assert result.checks_run > len(result.findings)
        assert 0.0 < result.alignment < 1.0

    def test_a_single_finding_does_not_score_zero(self):
        # The old arithmetic divided by the failures alone, so one finding on
        # an otherwise conforming ticket came out at 0% - the real board had a
        # ticket with one medium finding scored dead last because of it.
        p = corpus()
        without_checklist = make_ticket(
            "#602",
            # Checkboxes turned into plain bullets, and padded so this stays a
            # test about the checklist rather than about length.
            description=GOOD_BODY.replace("- [ ] ", "- ") + "\nMore context here.\n",
            labels=("bug", "team::shop"),
        )
        result = review(without_checklist, p)
        assert [f.code for f in result.findings] == ["no_checklist"]
        assert result.alignment > 0.8

    def test_every_finding_explains_itself(self):
        p = corpus()
        result = review(make_ticket("#508", description="Export broken.", state="open"), p)
        assert result.findings
        for finding in result.findings:
            assert finding.what and finding.why and finding.fix
            assert finding.severity in ("high", "medium", "low")
        severities = [f.severity for f in result.findings]
        assert severities == sorted(severities, key=lambda s: ["high", "medium", "low"].index(s))


class TestLimitsTravelWithTheProfile:
    """A profile built from a board that was only half readable says so.

    The run that builds it prints the caveat once; every later review reads the
    cached profile and would print nothing. A profile is a claim about how a
    team writes, and one built without comments is a weaker claim - which has
    to be attached to the claim, not to the run.
    """

    def limited(self):
        from ticket_ai_mcp.mining import score

        detail = make_detail(make_ticket("#1", description=GOOD_BODY))
        return build(
            [score(detail)],
            project="acme/shop",
            tracker="gitlab",
            limits=(
                (
                    "tracker_withheld",
                    {"host": "gitlab.com", "parts": ["notes"], "why": "anonymous"},
                ),
            ),
        )

    def test_the_note_is_on_the_profile(self):
        assert any("did not serve comments" in n for n in self.limited().notes)

    def test_and_it_can_be_read_back_in_german(self):
        # The whole reason a note is a code and not a sentence: this page has
        # a German mode, and a caveat is the last thing that should arrive in
        # English on it.
        german = self.limited().localised_notes("de")
        assert any("nicht geliefert" in n and "Kommentare" in n for n in german)

    def test_it_survives_being_cached_and_read_back(self):
        from ticket_ai_mcp.profile import Profile

        again = Profile.from_json(self.limited().to_json())
        assert any("did not serve comments" in n for n in again.notes)

    def test_an_empty_corpus_keeps_it_too(self):
        # The case that needs it most: nothing came back, and why nothing came
        # back is the whole answer.
        empty = build(
            [],
            project="acme/shop",
            tracker="gitlab",
            limits=(
                (
                    "tracker_withheld",
                    {"host": "gitlab.com", "parts": ["notes"], "why": "anonymous"},
                ),
            ),
        )
        assert any("did not serve comments" in n for n in empty.notes)


class TestARigidTemplateStaysWhole:
    """Six sections that always appear together are one block, not twelve rules.

    Measured on rollup/rollup: thirty rules cleared the threshold, which is
    every ordered pair of six sections. Truncating the list to twelve before
    clustering handed the cluster finder a graph missing half its edges - and
    a block is found by pairs that hold in *both* directions, so cutting one
    direction of a pair destroys the evidence for the block.
    """

    def corpus(self, headings: list[str], n: int = 10):
        """Half the board writes the block, half writes prose.

        A block that every ticket has is not conditional on anything: the
        baseline equals the conditional rate and no pair clears the threshold.
        The interesting board, and the one rollup/rollup turned out to be, is
        the one with two ticket shapes - the sections imply each other, and the
        board-wide rate of 50% hides all of it.
        """
        from ticket_ai_mcp.mining import score

        body = "\n\n".join(f"## {h}\nsomething about {h} here to give it length" for h in headings)
        plain = "A paragraph of prose with no heading in it at all, long enough to count."
        return [
            score(make_detail(make_ticket(f"#{i}", description=body))) for i in range(1, n + 1)
        ] + [score(make_detail(make_ticket(f"#p{i}", description=plain))) for i in range(1, n + 1)]

    def test_every_pair_of_a_rigid_block_survives_the_measurement(self):
        headings = ["Problem", "Ziel", "Umfang", "Kriterien", "Risiken", "Test"]
        profile = build(self.corpus(headings), project="acme/shop", tracker="gitlab")
        # Six sections, both directions: thirty ordered pairs, none of them
        # distinguishable from the others.
        assert len(profile.conditionals) == len(headings) * (len(headings) - 1)

    def test_and_the_block_is_recognised_as_one(self):
        headings = ["Problem", "Ziel", "Umfang", "Kriterien", "Risiken", "Test"]
        profile = build(self.corpus(headings), project="acme/shop", tracker="gitlab")
        blocks = clusters(profile.conditionals)
        assert len(blocks) == 1
        assert len(blocks[0]) == len(headings)

    def test_the_report_prints_the_block_once_instead_of_thirty_rules(self):
        headings = ["Problem", "Ziel", "Umfang", "Kriterien", "Risiken", "Test"]
        profile = build(self.corpus(headings), project="acme/shop", tracker="gitlab")
        text = render_profile(profile)
        assert text.count("of the time") == 0
        assert text.count("appear as a block") == 1

    def test_one_missing_section_is_one_finding_not_five(self):
        # The ticket under review has the block minus its last section. Every
        # other section implies the missing one, so before the fix this asked
        # for the same paragraph five times.
        headings = ["Problem", "Ziel", "Umfang", "Kriterien", "Risiken", "Test"]
        profile = build(self.corpus(headings), project="acme/shop", tracker="gitlab")
        body = "\n\n".join(
            f"## {h}\nsomething about {h} here to give it length" for h in headings[:-1]
        )
        found = review(make_ticket("#99", description=body, state="open"), profile)
        asked = [f for f in found.findings if f.code == "missing_conditional_section"]
        assert len(asked) == 1
        assert asked[0].params["then"] == "Test"


class TestTheReportSaysWhyTheCorpusIsSmall:
    """A corpus of two out of fifty needs its reason printed beside it.

    Found on Hibernate's HSEARCH board: the recent closed tickets are almost
    all dependency bumps with no description, so 48 of 50 were excluded and
    the report said "built from 2 tickets" and stopped. From the outside that
    is indistinguishable from a filter that is too strict, and the tool had
    the reason in hand the whole time.
    """

    def gathered(self, keep: int, drop: int):
        from ticket_ai_mcp.corpus import by_mining

        good = [make_detail(make_ticket(f"#{i}", description=GOOD_BODY)) for i in range(keep)]
        thin = [make_detail(make_ticket(f"#t{i}", description="too short")) for i in range(drop)]

        class Board:
            name = "test"

            def search(self, query):
                return [d.ticket for d in good + thin]

            def fetch(self, project, key):  # pragma: no cover - unused
                raise NotImplementedError

            def detail(self, ticket):
                return next(d for d in good + thin if d.ticket.key == ticket.key)

        return by_mining(Board(), "acme/shop", sample=99, want=99)

    def test_the_reasons_are_grouped_and_counted(self):
        gathered = self.gathered(keep=6, drop=20)
        profile = build(gathered.exemplars, project="acme/shop", tracker="gitlab")
        text = render_profile(profile, gathered)
        assert "## Left out" in text
        assert "20 of 26" in text

    def test_an_empty_corpus_says_why_too(self):
        # The branch that needs it most, and the one that used to return
        # before the reasons were reached: nothing measured, no explanation.
        gathered = self.gathered(keep=0, drop=12)
        profile = build(gathered.exemplars, project="acme/shop", tracker="gitlab")
        text = render_profile(profile, gathered)
        assert "Nothing could be measured" in text
        assert "12 of 12" in text


class TestAProfileCachedBeforeThisChange:
    """The cache on disk outlives the code that wrote it.

    A profile is cached next to somebody's checkout and nobody asked it to
    expire. Notes used to be finished English sentences under a `notes` key;
    they are codes now, and a loader that had not been told would have raised
    TypeError on the old key - turning a background cache read into a crash on
    a machine where nothing changed but the version.
    """

    OLD = json.dumps(
        {
            "project": "acme/shop",
            "tracker": "gitlab",
            "built_at": "2026-01-01T00:00:00+00:00",
            "sample_size": 3,
            "exemplar_keys": ["#1", "#2", "#3"],
            "notes": ["Built from 3 tickets, so treat every rate as a hint."],
        }
    )

    def test_it_still_loads(self):
        again = Profile.from_json(self.OLD)
        assert again.sample_size == 3

    def test_and_the_caveat_survives_verbatim(self):
        # It cannot be translated - nobody kept the parts - but a caveat that
        # disappears on upgrade is the failure this whole change was about.
        again = Profile.from_json(self.OLD)
        assert again.notes == ("Built from 3 tickets, so treat every rate as a hint.",)
        assert again.localised_notes("de") == again.notes

    def test_and_it_round_trips_forward(self):
        again = Profile.from_json(Profile.from_json(self.OLD).to_json())
        assert again.notes == ("Built from 3 tickets, so treat every rate as a hint.",)


class TestAPathologicalCorpusCannotHangIt:
    """Every ordered pair of triggers is examined, and nothing bounded the count.

    `learn_conventions` is an MCP call somebody is waiting on, and the work
    grew with the square of the number of recurring headings: measured over a
    corpus of twelve tickets, 500 headings cost 0.3 seconds, 2000 cost 3.5,
    3000 cost ten, and it kept going.

    Not a fuzzing curiosity - a ticket template that generates headings, or a
    board that pastes a table of contents into every ticket, gets there
    without anybody trying.
    """

    def corpus(self, headings: int, tickets: int = 12):
        body = "".join(f"## H{i}\nsomething here\n" for i in range(headings))
        return [score(make_detail(make_ticket(f"#{i}", description=body))) for i in range(tickets)]

    def test_two_thousand_headings_finish_promptly(self):
        """The number and the budget are both measured, not guessed.

        A/B against the same corpus: with the ceiling, 2000 recurring headings
        cost 0.17 seconds; without it, 3.32. A second sat well above the one
        and well below the other.

        The first version of this test used a thousand headings and a
        two-second budget - and a thousand costs 0.94 seconds uncapped, so it
        passed with the ceiling and without it, and proved nothing.

        **The budget moved to two seconds because the original was measured
        without coverage and CI runs with it.** Six runs under
        `--cov=ticket_ai_mcp` came in at 0.57, 0.60, 0.61, 0.61, 0.67 and 0.97
        seconds, so the old budget had one run at 97% of it and this failed
        once in a full suite. Two seconds keeps the gap to the uncapped 3.32
        that the test exists to catch, and stops it firing on a loaded runner.
        A timing test that goes red for being unlucky teaches people to rerun
        CI, which is the opposite of what it is for.
        """
        started = time.monotonic()
        build(self.corpus(2000), project="acme/shop", tracker="gitlab")
        assert time.monotonic() - started < 2.0

    def test_the_cap_is_far_above_any_real_board(self):
        # Measured across the fleet: the widest board produced eleven triggers
        # and the one with the most sections had twenty-five. If this ever has
        # to come down, that is the number to check it against.
        assert MAX_TRIGGERS >= 50

    def test_a_normal_board_is_untouched_by_it(self):
        # Ten headings, well under the ceiling: every pair is still examined.
        profile = build(self.corpus(10, tickets=20), project="acme/shop", tracker="gitlab")
        assert len(profile.sections) == 10
