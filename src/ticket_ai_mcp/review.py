# SPDX-License-Identifier: MIT

"""Measuring one ticket against what the team already does.

Every finding in this file has to cite a number from the profile. That rule is
what separates this from a linter with opinions, and it is worth stating in
code because it is easy to erode: the first hardcoded "descriptions should be
at least 300 characters" turns the tool back into generic advice nobody asked
for.

The output is called **alignment**, not quality, and the distinction is not
pedantry. A ticket can match the house template perfectly and still be a bad
idea, and a one-line ticket from someone who knows exactly what they mean can
be fine. What this measures is how far a ticket sits from the ones that
historically got built here - useful, and not the same as good.

Findings are ordered by severity and then by how strong the evidence is, so the
first thing a reader sees is the thing the corpus is most confident about.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from .profile import Profile
from .schemas import Ticket
from .textstats import language, normalise_heading, shape

# How common a habit has to be in the corpus before its absence is worth
# mentioning. Below this a "convention" is just a preference some people have.
EXPECT_RATE = 0.6
# And how common before it is worth raising the severity.
STRONG_RATE = 0.85

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


@dataclass(frozen=True, slots=True)
class Finding:
    """One way this ticket departs from the corpus.

    `why` always names the measurement. Without it a reader has no way to tell
    a house rule from the tool's taste, and no way to push back when the corpus
    is wrong.
    """

    code: str
    severity: str
    what: str
    why: str
    fix: str


@dataclass(frozen=True, slots=True)
class Review:
    """The result of comparing one ticket with the profile.

    `alignment` is **None** when no check applied, and that is not the same as
    1.0. A team whose tickets have no recurring template and inconsistent
    labels disables almost every check here, and reporting that ticket as
    "100%, nothing to flag" is a lie the tool told itself before it told
    anyone else. `checks_run` says how many actually fired.
    """

    ticket_key: str
    ticket_url: str
    alignment: float | None
    findings: tuple[Finding, ...]
    passed: tuple[str, ...] = ()
    caveats: tuple[str, ...] = field(default_factory=tuple)
    checks_run: int = 0


def _pct(rate: float) -> str:
    return f"{round(rate * 100)}%"


def review_draft(
    title: str,
    description: str,
    profile: Profile,
    *,
    labels: tuple[str, ...] = (),
) -> Review:
    """Measure a ticket that has not been created yet.

    This closes the loop the rest of the tool leaves open. Without it the only
    way to check a ticket is to create it first, which puts the review after
    the point of no return: the draft is already on the board, already
    notified whoever watches it, and fixing it is now an edit with a history.

    The draft is wrapped in a Ticket so it goes through exactly the same
    checks. A separate code path for drafts would drift, and then a draft
    would pass what the created ticket fails.
    """
    now = datetime.now(UTC)
    draft = Ticket(
        uid="draft",
        key="(draft)",
        title=title,
        description=description,
        state="open",
        labels=labels,
        author="",
        assignees=(),
        created_at=now,
        updated_at=now,
        closed_at=None,
        url="",
        tracker=profile.tracker,
        project=profile.project,
    )
    return review(draft, profile)


def review(ticket: Ticket, profile: Profile) -> Review:
    """Compare one ticket with the profile built from the team's own tickets."""
    body = shape(ticket.description)
    findings: list[Finding] = []
    passed: list[str] = []
    caveats: list[str] = list(profile.notes)

    # Every check that *applied* is recorded here, whether it passed or not.
    # Scoring only over the checks that failed is what produced a board where
    # 35 of 40 tickets read "100%, nothing to flag" - the checks had all been
    # disabled by a corpus with no strong conventions, and silence was being
    # reported as a clean bill of health.
    checks: list[tuple[float, bool]] = []
    weight_of = {"high": 3.0, "medium": 2.0, "low": 1.0}

    def record(severity: str, ok: bool) -> None:
        checks.append((weight_of[severity], ok))

    if profile.sample_size == 0:
        return Review(
            ticket_key=ticket.key,
            ticket_url=ticket.url,
            alignment=None,
            findings=(),
            caveats=(
                "There is no profile to compare against - no exemplar tickets were found. "
                "Nothing below this line would mean anything.",
            ),
        )

    if body.is_empty:
        findings.append(
            Finding(
                code="empty_description",
                severity="high",
                what="The ticket has no description at all.",
                why=(
                    f"Every one of the {profile.sample_size} tickets that shipped in "
                    f"{profile.project} had one, with a median of {round(profile.chars_median)} "
                    "characters."
                ),
                fix="Write the description before anything else here is worth checking.",
            )
        )
        # Everything downstream measures a description. There isn't one.
        return Review(
            ticket_key=ticket.key,
            ticket_url=ticket.url,
            alignment=0.0,
            findings=tuple(findings),
            caveats=tuple(caveats),
            checks_run=1,
        )

    # --- structure -----------------------------------------------------
    present = {normalise_heading(h) for h in body.headings}
    for section in profile.sections:
        if section.rate < EXPECT_RATE:
            continue
        severity = "high" if section.rate >= STRONG_RATE else "medium"
        record(severity, section.key in present)
        if section.key in present:
            passed.append(f"has the {section.heading} section")
            continue
        findings.append(
            Finding(
                code="missing_section",
                severity=severity,
                what=f"No {section.heading!r} section.",
                why=(
                    f"{section.count} of the {profile.sample_size} exemplar tickets "
                    f"({_pct(section.rate)}) have one."
                ),
                fix=f"Add the {section.heading!r} heading and fill it in.",
            )
        )

    # A convention that only holds on some tickets is invisible in the
    # board-wide rate, so it is checked separately and only on the tickets it
    # applies to. This is what makes a team with two ticket shapes checkable at
    # all - see `profile.Conditional`.
    # A block of sections implies itself in both directions, so a ticket that
    # has all of them satisfies each pair twice. Scored twice that is fine -
    # it is genuinely two checks - but reported twice it reads as a stutter.
    said: set[frozenset[str]] = set()
    for rule in profile.conditionals:
        if rule.when not in present or rule.rate < EXPECT_RATE:
            continue
        record("medium", rule.then in present)
        if rule.then in present:
            pair = frozenset((rule.when, rule.then))
            if pair not in said:
                said.add(pair)
                passed.append(f"has {rule.then_heading} to go with {rule.when_heading}")
            continue
        findings.append(
            Finding(
                code="missing_conditional_section",
                severity="medium",
                what=f"Has a {rule.when_heading!r} section but no {rule.then_heading!r}.",
                why=(
                    f"{rule.count} of the {rule.of} exemplar tickets with a "
                    f"{rule.when_heading!r} section ({_pct(rule.rate)}) also have "
                    f"{rule.then_heading!r} - against {_pct(rule.baseline)} of tickets overall. "
                    "The two go together on this board."
                ),
                fix=f"Add the {rule.then_heading!r} section.",
            )
        )

    # --- length --------------------------------------------------------
    record("medium", body.chars >= profile.chars_p25)
    if body.chars < profile.chars_p25:
        findings.append(
            Finding(
                code="short_description",
                severity="medium",
                what=f"The description is {body.chars} characters.",
                why=(
                    f"The shortest quarter of tickets that shipped here start at "
                    f"{round(profile.chars_p25)} characters; the median is "
                    f"{round(profile.chars_median)}."
                ),
                fix="Say what should happen, and how anyone will know it did.",
            )
        )
    else:
        passed.append(f"description length ({body.chars} characters) is in the normal range")

    # --- labels --------------------------------------------------------
    if profile.label_rate >= EXPECT_RATE:
        record("medium", bool(ticket.labels))
    if profile.label_rate >= EXPECT_RATE and not ticket.labels:
        findings.append(
            Finding(
                code="no_labels",
                severity="medium",
                what="The ticket has no labels.",
                why=f"{_pct(profile.label_rate)} of the exemplars are labelled.",
                fix=(
                    "Add the usual ones: "
                    + ", ".join(label for label, _ in profile.common_labels[:5])
                    if profile.common_labels
                    else "Add the labels this project sorts by."
                ),
            )
        )
    elif ticket.labels:
        passed.append(f"labelled ({', '.join(ticket.labels[:4])})")

    # A scoped label group that almost every ticket carries is usually the one
    # the board columns are built from, so a ticket missing it falls off the
    # board rather than merely looking untidy.
    #
    # Skipped entirely when the ticket has no labels at all: `no_labels` above
    # already said that, and naming each missing group on top of it turns one
    # problem into four findings that all have the same fix.
    for group in profile.label_groups if ticket.labels else ():
        group_rate = sum(
            rate for label, rate in profile.common_labels if label.startswith(f"{group}::")
        )
        if group_rate < EXPECT_RATE:
            continue
        has_group = any(label.startswith(f"{group}::") for label in ticket.labels)
        record("medium", has_group)
        if has_group:
            continue
        findings.append(
            Finding(
                code="missing_label_group",
                severity="medium",
                what=f"No {group}:: label.",
                why=f"Roughly {_pct(min(group_rate, 1.0))} of the exemplars carry one.",
                fix=f"Set the {group}:: label so the board picks this up.",
            )
        )

    # --- habits --------------------------------------------------------
    for code, rate, has, what, fix in (
        (
            "no_checklist",
            profile.checkbox_rate,
            body.checkboxes > 0,
            "No checklist.",
            "Break the work into checkboxes the way the other tickets do.",
        ),
        (
            "no_screenshot",
            profile.image_rate,
            body.images > 0,
            "No screenshot or attachment.",
            "Attach the screen this is about.",
        ),
        (
            "no_cross_reference",
            profile.cross_ref_rate,
            bool(body.ticket_refs),
            "Nothing linked to another ticket or change.",
            "Link the ticket this follows from, if there is one.",
        ),
    ):
        if rate < STRONG_RATE:
            continue
        record("low", has)
        if has:
            passed.append(what.rstrip(".").replace("No ", "has a ").lower())
            continue
        findings.append(
            Finding(
                code=code,
                severity="low",
                what=what,
                why=f"{_pct(rate)} of the exemplars have one.",
                fix=fix,
            )
        )

    # --- title ---------------------------------------------------------
    for prefix, rate in profile.title_prefixes:
        if rate < EXPECT_RATE:
            continue
        marked = ticket.title.strip().startswith(prefix.rstrip(":/- ").strip("[]"))
        record("low", marked)
        if not marked:
            findings.append(
                Finding(
                    code="title_prefix",
                    severity="low",
                    what=f"The title does not start with a marker like {prefix!r}.",
                    why=f"{_pct(rate)} of the exemplar titles do.",
                    fix=f"Prefix the title the way the others are prefixed ({prefix}).",
                )
            )
        break

    # --- language ------------------------------------------------------
    ticket_language = language(f"{ticket.title}\n{ticket.description}")
    if profile.language and ticket_language:
        record("medium", ticket_language == profile.language)
    if profile.language and ticket_language and ticket_language != profile.language:
        findings.append(
            Finding(
                code="language_mismatch",
                severity="medium",
                what=f"Written in {ticket_language}.",
                why=f"The exemplar tickets in this project are in {profile.language}.",
                fix="Match the language the rest of the board is in.",
            )
        )

    findings.sort(key=lambda f: (_SEVERITY_ORDER[f.severity], f.code))

    # The share of applicable weight that passed. Severity is the weight, so a
    # missing template section costs more than a missing screenshot.
    total = sum(weight for weight, _ in checks)
    if total == 0:
        # Nothing was measurable. Say that, rather than dressing an absence of
        # evidence up as a perfect score.
        alignment = None
        caveats.append(
            "No check applied to this ticket: the exemplar tickets have no convention "
            "consistent enough to hold anyone to. That is a finding about the board, "
            "not a pass for this ticket."
        )
    else:
        earned = sum(weight for weight, ok in checks if ok)
        alignment = round(earned / total, 3)

    return Review(
        ticket_key=ticket.key,
        ticket_url=ticket.url,
        alignment=alignment,
        findings=tuple(findings),
        passed=tuple(passed),
        caveats=tuple(caveats),
        checks_run=len(checks),
    )
