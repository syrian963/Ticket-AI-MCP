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
from typing import Any

from . import messages
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

    **Data, not prose.** A finding carries a code and the measurements behind
    it; the sentence is built when it is displayed, in whatever language the
    reader asked for. Building the sentence here would mean one language
    forever, and a German page reading *Übereinstimmung mit dem Hausstil*
    followed by *The ticket has no description at all* is not carrying a number
    carefully - it is half-translated the other way round.

    `what`, `why` and `fix` render English, which is what every caller that has
    not asked for a language gets. `localised` is the same three in any
    supported one, and both go through the single catalogue in `messages.py`,
    so a translation cannot quietly say something the English does not.

    `why` always names the measurement. Without it a reader cannot tell a house
    rule from the tool's taste, and cannot push back when the corpus is wrong.
    """

    code: str
    severity: str
    params: dict[str, Any] = field(default_factory=dict)

    def _text(self, part: str, language: str | None = None) -> str:
        return messages.render(self.code, part, self.params, language)

    @property
    def what(self) -> str:
        return self._text("what")

    @property
    def why(self) -> str:
        return self._text("why")

    @property
    def fix(self) -> str:
        return self._text("fix")

    def localised(self, language: str | None = None) -> tuple[str, str, str]:
        """what, why and fix, in one language."""
        return (
            self._text("what", language),
            self._text("why", language),
            self._text("fix", language),
        )


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
    # What the ticket already got right, and what to distrust about the
    # comparison - both as a code and its measurements, the way findings are.
    # They were English prose, so a German review showed German findings and
    # then "has the Steps to reproduce section" under a German heading.
    passed_codes: tuple[tuple[str, dict[str, Any]], ...] = ()
    caveat_codes: tuple[tuple[str, dict[str, Any]], ...] = ()
    checks_run: int = 0

    @property
    def passed(self) -> tuple[str, ...]:
        return self.localised_passed()

    @property
    def caveats(self) -> tuple[str, ...]:
        return self.localised_caveats()

    def localised_passed(self, language: str | None = None) -> tuple[str, ...]:
        from .messages import render_passed

        return tuple(render_passed(code, params, language) for code, params in self.passed_codes)

    def localised_caveats(self, language: str | None = None) -> tuple[str, ...]:
        from .messages import render_note, render_passed

        out = []
        for code, params in self.caveat_codes:
            # A caveat is either one of this module's own or a note carried
            # over from the profile; both catalogues answer with the code
            # itself when they do not know it, so trying both is safe.
            rendered = render_passed(code, params, language)
            if rendered == code:
                rendered = render_note(code, params, language)
            out.append(rendered)
        return tuple(out)


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
    passed: list[tuple[str, dict[str, Any]]] = []
    caveats: list[tuple[str, dict[str, Any]]] = list(profile.note_codes)

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
            caveat_codes=(("no_profile", {}),),
        )

    if body.is_empty:
        findings.append(
            Finding(
                code="empty_description",
                severity="high",
                params={
                    "total": profile.sample_size,
                    "project": profile.project,
                    "median": round(profile.chars_median),
                },
            )
        )
        # Everything downstream measures a description. There isn't one.
        return Review(
            ticket_key=ticket.key,
            ticket_url=ticket.url,
            alignment=0.0,
            findings=tuple(findings),
            caveat_codes=tuple(caveats),
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
            passed.append(("has_section", {"heading": section.heading}))
            continue
        # Where the shipped-against-stalled comparison had enough of both to be
        # worth quoting, quote it: two rates are an argument and one is a rate.
        contrast = profile.discriminative.get(section.key)
        if contrast:
            shipped_rate, stalled_rate = contrast
            findings.append(
                Finding(
                    code="missing_section_contrast",
                    severity=severity,
                    params={
                        "heading": section.heading,
                        "shipped": _pct(shipped_rate),
                        "stalled": _pct(stalled_rate),
                    },
                )
            )
            continue
        findings.append(
            Finding(
                code="missing_section",
                severity=severity,
                params={
                    "heading": section.heading,
                    "count": section.count,
                    "total": profile.sample_size,
                    "pct": _pct(section.rate),
                },
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
    missing: set[str] = set()
    for rule in profile.conditionals:
        if rule.when not in present or rule.rate < EXPECT_RATE:
            continue
        record("medium", rule.then in present)
        if rule.then in present:
            pair = frozenset((rule.when, rule.then))
            if pair not in said:
                said.add(pair)
                passed.append(("has_pair", {"then": rule.then_heading, "when": rule.when_heading}))
            continue
        # One missing section, one finding. A ticket that skipped the
        # reproduction on a board where five other sections all imply it used
        # to collect five findings that asked for the same paragraph. The
        # rules are sorted strongest first, so the first one to name a section
        # is the one with the best evidence behind it.
        if rule.then in missing:
            continue
        missing.add(rule.then)
        findings.append(
            Finding(
                code="missing_conditional_section",
                severity="medium",
                params={
                    "when": rule.when_heading,
                    "then": rule.then_heading,
                    "count": rule.count,
                    "of": rule.of,
                    "pct": _pct(rule.rate),
                    "baseline": _pct(rule.baseline),
                },
            )
        )

    # --- length --------------------------------------------------------
    record("medium", body.chars >= profile.chars_p25)
    if body.chars < profile.chars_p25:
        findings.append(
            Finding(
                code="short_description",
                severity="medium",
                params={
                    "chars": body.chars,
                    "p25": round(profile.chars_p25),
                    "median": round(profile.chars_median),
                },
            )
        )
    else:
        passed.append(("length_normal", {"chars": body.chars}))

    # --- labels --------------------------------------------------------
    if profile.label_rate >= EXPECT_RATE:
        record("medium", bool(ticket.labels))
    if profile.label_rate >= EXPECT_RATE and not ticket.labels:
        findings.append(
            Finding(
                code="no_labels",
                severity="medium",
                params={
                    "pct": _pct(profile.label_rate),
                    "suggestions": ", ".join(label for label, _ in profile.common_labels[:5])
                    or "whatever this project sorts by",
                },
            )
        )
    elif ticket.labels:
        passed.append(("labelled", {"labels": ", ".join(ticket.labels[:4])}))

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
                params={"group": group, "pct": _pct(min(group_rate, 1.0))},
            )
        )

    # --- habits --------------------------------------------------------
    for code, rate, has, done in (
        ("no_checklist", profile.checkbox_rate, body.checkboxes > 0, "has_checklist"),
        ("no_screenshot", profile.image_rate, body.images > 0, "has_image"),
        (
            "no_cross_reference",
            profile.cross_ref_rate,
            bool(body.ticket_refs),
            "has_cross_ref",
        ),
    ):
        if rate < STRONG_RATE:
            continue
        record("low", has)
        if has:
            passed.append((done, {}))
            continue
        findings.append(Finding(code=code, severity="low", params={"pct": _pct(rate)}))

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
                    params={"prefix": prefix, "pct": _pct(rate)},
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
                params={"found": ticket_language, "expected": profile.language},
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
        caveats.append(("nothing_applied", {}))
    else:
        earned = sum(weight for weight, ok in checks if ok)
        alignment = round(earned / total, 3)

    return Review(
        ticket_key=ticket.key,
        ticket_url=ticket.url,
        alignment=alignment,
        findings=tuple(findings),
        passed_codes=tuple(passed),
        caveat_codes=tuple(caveats),
        checks_run=len(checks),
    )
