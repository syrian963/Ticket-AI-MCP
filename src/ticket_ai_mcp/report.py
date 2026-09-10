# SPDX-License-Identifier: MIT

"""Turning a profile or a review into something worth reading.

Markdown, one renderer, used by both front ends. A terminal shows it fine and
an assistant can hand it straight to a person, which is most of what these
reports are for - the useful move after a review is usually pasting it into the
ticket.

The ordering rule throughout: the number that justifies a claim goes in the
same sentence as the claim. A reader who has to scroll to find out where "78%"
came from will not scroll.
"""

from __future__ import annotations

from collections import Counter

from .context import Context
from .corpus import Gathered
from .profile import Profile, clusters
from .review import Review

# How many loose conditional rules a report prints. A reader who has read a
# dozen has the idea; the rest are in the profile for the reviewer to use.
SHOW_CONDITIONALS = 12


def _pct(rate: float) -> str:
    return f"{round(rate * 100)}%"


def _left_out(gathered: Gathered | None) -> list[str]:
    """Why the corpus is the size it is.

    The reasons were being collected and then dropped on the floor, which
    mattered most exactly where a reader needs them: a Hibernate board came
    back "built from 2 tickets" with no hint that the other 48 were dependency
    bumps carrying no description at all. Two tickets is a fine reason to
    distrust a profile, and a reader who is not told why cannot tell a strict
    filter from a quiet board.
    """
    if not gathered or not gathered.rejected:
        return []
    reasons = Counter(r.reason.split(" - ")[0] for r in gathered.rejected)
    lines = ["## Left out", ""]
    lines += [
        f"- {count} of {gathered.considered}: {reason}" for reason, count in reasons.most_common(5)
    ]
    lines.append("")
    return lines


def render_profile(profile: Profile, gathered: Gathered | None = None) -> str:
    lines: list[str] = [
        f"# House style: {profile.project}",
        "",
        f"Built from {profile.sample_size} tickets"
        + (f" out of {gathered.considered} considered" if gathered else "")
        + (f", drawn from {gathered.source}" if gathered else "")
        + ".",
        "",
    ]

    if profile.sample_size == 0:
        # The one report where the reason is the entire content. "No ticket in
        # the sample could be used as an exemplar" over thirty considered
        # tickets reads like a broken tool; "29 of 30: description is under 80
        # characters" reads like a board of dependency bumps, which is what it
        # was. This branch used to return before the reasons were printed.
        lines += ["Nothing could be measured.", ""]
        lines += [f"- {note}" for note in profile.notes]
        lines.append("")
        lines += _left_out(gathered)
        return "\n".join(lines)

    if profile.sections:
        lines += ["## The template", ""]
        for section in profile.sections:
            if section.rate < 0.2:
                continue
            lines.append(
                f"- **{section.heading}** - {section.count}/{profile.sample_size} "
                f"({_pct(section.rate)})"
            )
        lines.append("")
    else:
        lines += ["## The template", "", "No heading recurs. This team writes prose.", ""]

    strong = [c for c in profile.conditionals if c.rate >= 0.6]
    if strong:
        lines += [
            "## Sections that travel together",
            "",
            "These are conventions the board-wide rates above hide: they hold on "
            "one kind of ticket, not on all of them.",
            "",
        ]
        blocks = clusters(profile.conditionals)
        in_block = {heading for block in blocks for heading in block}
        for block in blocks:
            names = ", ".join(f"**{name}**" for name in block)
            lines.append(f"- these appear as a block: {names}")
        # Truncation belongs here rather than in the measurement. A board whose
        # sections all imply each other produces one line above and does not
        # need thirty below it; a board that does not is worth a dozen.
        loose = [
            rule
            for rule in strong
            if not (rule.when_heading in in_block and rule.then_heading in in_block)
        ]
        for rule in loose[:SHOW_CONDITIONALS]:
            lines.append(
                f"- a ticket with **{rule.when_heading}** has **{rule.then_heading}** "
                f"{rule.count}/{rule.of} of the time ({_pct(rule.rate)}), "
                f"against {_pct(rule.baseline)} overall"
            )
        lines.append("")

    lines += [
        "## Length",
        "",
        f"- median {round(profile.chars_median)} characters",
        f"- the shortest quarter start at {round(profile.chars_p25)}",
        f"- the longest quarter start at {round(profile.chars_p75)}",
        "",
        "## Habits",
        "",
        f"- labelled: {_pct(profile.label_rate)} "
        f"(median {round(profile.labels_per_ticket_median)} labels)",
        f"- assigned: {_pct(profile.assignee_rate)}",
        f"- a list: {_pct(profile.list_rate)}",
        f"- a checklist: {_pct(profile.checkbox_rate)}",
        f"- a code block: {_pct(profile.code_rate)}",
        f"- a screenshot: {_pct(profile.image_rate)}",
        f"- links another ticket: {_pct(profile.cross_ref_rate)}",
        "",
    ]

    if profile.common_labels:
        lines += ["## Labels in use", ""]
        lines += [f"- `{label}` - {_pct(rate)}" for label, rate in profile.common_labels[:10]]
        lines.append("")

    if profile.title_prefixes:
        lines += ["## Title markers", ""]
        lines += [f"- `{prefix}` - {_pct(rate)}" for prefix, rate in profile.title_prefixes]
        lines.append("")

    if profile.language:
        lines += [f"Tickets here are written in **{profile.language}**.", ""]

    if profile.notes:
        lines += ["## Read this first", ""]
        lines += [f"- {note}" for note in profile.notes]
        lines.append("")

    lines += [
        "## Measured from",
        "",
        ", ".join(profile.exemplar_keys),
        "",
    ]

    lines += _left_out(gathered)

    if gathered and (gathered.errors or gathered.limits):
        lines += ["## Could not read", ""]
        lines += [f"- {e}" for e in gathered.limits]
        lines += [f"- {e}" for e in gathered.errors[:10]]
        lines.append("")

    return "\n".join(lines)


def render_contrast(contrast) -> str:
    """What separated the tickets that shipped from the ones that stalled.

    Ordered by the size of the gap, because the gap is the claim. A rate on
    its own invites a shrug; a rate next to the rate for tickets that did not
    get built is an argument.
    """
    if not contrast.usable:
        lines = ["## Shipped against stalled", ""]
        lines += [f"{note}" for note in contrast.notes]
        return "\n".join(lines) + "\n"

    lines = [
        "## Shipped against stalled",
        "",
        f"{contrast.shipped} tickets shipped cleanly - a change merged, no reopens, "
        f"no run of clarifying questions. {contrast.stalled} stalled. The split is "
        "on what happened to them, never on how they were written, so the "
        "difference below is evidence rather than arithmetic.",
        "",
    ]
    helps = [s for s in contrast.signals if s.helps]
    hurts = [s for s in contrast.signals if not s.helps]

    if helps:
        lines += ["| Present in the ones that shipped | Shipped | Stalled |", "|---|---|---|"]
        for s in helps[:10]:
            lines.append(f"| {s.label} | {_pct(s.shipped_rate)} | {_pct(s.stalled_rate)} |")
        lines.append("")
    if hurts:
        lines += [
            "More common on the ones that stalled:",
            "",
        ]
        lines += [
            f"- {s.label} - {_pct(s.stalled_rate)} of stalled, {_pct(s.shipped_rate)} of shipped"
            for s in hurts[:6]
        ]
        lines += [
            "",
            "Worth reading twice before acting on. A feature commoner among the "
            "tickets that stalled is as likely to be a symptom as a cause - hard "
            "problems attract long descriptions.",
            "",
        ]
    lines += [f"{note}" for note in contrast.notes]
    return "\n".join(lines) + "\n"


def render_gaps(declared, gaps, undeclared, sample: int) -> str:
    """The form the project declares, against the tickets it actually gets.

    The gap is the finding, in both directions. A required field almost nobody
    fills in is a form asking for something people cannot supply - that is a
    fix to the form, not a discipline problem. A heading most tickets carry
    that no form mentions is a convention the project grew and never wrote
    down, and it is the first thing to add.
    """
    if not declared.files:
        # From the list the search actually uses. Written out by hand here
        # once, and the copy had lost the leading dots on both paths - so the
        # one message whose entire job is to say where to put the file sent
        # people to two directories that cannot exist.
        from .templates import SEARCH

        return (
            "No issue template found in the checkout. Looked in "
            + ", ".join(f"`{p}`" for p in SEARCH)
            + ". Without one the measured profile is the only contract there is.\n"
        )

    lines = [
        "# Declared, and practised",
        "",
        f"From {', '.join('`' + f + '`' for f in declared.files)}, "
        f"against {sample} tickets that shipped.",
        "",
        "| | Field | Filled in |",
        "|---|---|---|",
    ]
    for gap in sorted(gaps, key=lambda g: g.rate):
        mark = "**required**" if gap.required else "optional"
        lines.append(f"| {mark} | {gap.label} | {_pct(gap.rate)} |")
    lines.append("")

    ignored = [g for g in gaps if g.required and g.is_ignored]
    if ignored:
        lines += [
            "## Asked for, and mostly not supplied",
            "",
        ]
        # Named by file, because a project usually has more than one form and
        # this is measured against one corpus. On home-assistant/core the task
        # form requires a `Description` that no bug ticket carries: true, and
        # unreadable until you can see it came from a form those tickets never
        # used.
        lines += [
            f"- **{g.label}** is required by "
            + (f"`{g.source}`" if g.source else "the form")
            + f" and appears in {_pct(g.rate)} of tickets."
            for g in ignored
        ]
        lines += [
            "",
            "A required field this far under is usually a form asking for something "
            "people cannot easily give. Changing the form is the cheaper fix - unless "
            "the field belongs to a form these tickets do not use, which a project "
            "with several of them will see here first.",
            "",
        ]

    if undeclared:
        lines += [
            "## Written by habit, declared nowhere",
            "",
            "Sections most tickets carry that no template asks for. These are "
            "conventions the project grew; adding them to the form is how they "
            "survive the next person who joins.",
            "",
        ]
        lines += [f"- {heading}" for heading in undeclared]
        lines.append("")

    return "\n".join(lines)


def render_context(context: Context) -> str:
    """The evidence, laid out for whoever is about to write the ticket.

    Ordered by how hard each part is to get any other way. The files past
    merge requests touched come first because nothing else can produce them;
    the codebase search comes last because a reader with a checkout could have
    done it themselves.
    """
    lines = [f"# Context for: {context.subject}", ""]

    if context.touched:
        lines += [
            "## Where this kind of work has landed before",
            "",
            "Files the merge requests for related tickets actually changed, most "
            "frequently first. This comes from the tracker's history - it cannot be "
            "inferred from the code.",
            "",
        ]
        lines += [f"- `{path}`" for path in context.touched[:15]]
        lines.append("")

    if context.prior:
        lines += ["## Related tickets", ""]
        for art in context.prior:
            ticket = art.match.ticket
            lines.append(f"- **{ticket.key}** {ticket.title}")
            lines.append(
                f"  matched on: {', '.join(art.match.shared)} "
                f"({art.match.score:.2f}) - {ticket.url}"
            )
            if art.files:
                lines.append(f"  changed {len(art.files)} files")
        lines.append("")
        lines += [
            "Check these before writing: one of them may already be this ticket.",
            "",
        ]
    elif context.searched:
        lines += [
            f"No related ticket among the last {context.searched} closed ones. This looks new.",
            "",
        ]

    if context.files:
        # The directory is named in the heading, not in a footnote. Searching
        # the wrong checkout produces confident nonsense - pointed at its own
        # source tree, this listed a Python file as relevant to a Rust
        # project's lockfile bug - and the only defence is saying out loud
        # which directory the answer came from.
        lines += [f"## Files in `{context.repo}` that mention it", ""]
        for hit in context.files:
            why = "filename" if hit.in_name else "contents"
            terms = ", ".join(hit.in_name or hit.in_body)
            lines.append(f"- `{hit.path}` ({hit.lines} lines) - {why}: {terms}")
        lines += [
            "",
            "A text search, not an analysis: some of these merely share a word. "
            "If that directory is not the checkout for this project, ignore the "
            "whole section.",
            "",
        ]

    if context.notes:
        lines += ["## Gaps", ""]
        lines += [f"- {note}" for note in context.notes]
        lines.append("")

    return "\n".join(lines)


def _alignment(review: Review) -> str:
    if review.alignment is None:
        return "**not measurable** - no convention was consistent enough to check"
    return f"**{_pct(review.alignment)}** over {review.checks_run} checks"


def render_review(review: Review) -> str:
    lines = [
        f"# {review.ticket_key}",
        "",
        f"Alignment with house style: {_alignment(review)}",
        "",
    ]
    if review.ticket_url:
        lines += [review.ticket_url, ""]

    if review.caveats:
        lines += ["> " + c for c in review.caveats] + [""]

    if not review.findings:
        lines += ["Nothing to flag. This matches how the team writes tickets.", ""]
    else:
        lines += ["## Findings", ""]
        for finding in review.findings:
            lines += [
                f"### {finding.severity.upper()} - {finding.what}",
                "",
                finding.why,
                "",
                f"*Fix:* {finding.fix}",
                "",
            ]

    if review.passed:
        lines += ["## Already right", ""]
        lines += [f"- {p}" for p in review.passed]
        lines.append("")

    return "\n".join(lines)


def render_batch(reviews: list[Review]) -> str:
    """A worklist, worst first.

    Sorted by alignment rather than by ticket number, because the only reason
    to read a page of these is to decide what to fix before standup.
    """
    if not reviews:
        return "No open tickets to review.\n"
    # An unmeasurable ticket sorts last, not first: there is no evidence
    # against it, and putting it at the top of a worklist would send someone
    # to fix a ticket nothing was ever checked on.
    ordered = sorted(reviews, key=lambda r: (r.alignment is None, r.alignment or 0.0, r.ticket_key))
    lines = [f"# {len(ordered)} open tickets, least aligned first", ""]
    unmeasured = 0
    for r in ordered:
        if r.alignment is None:
            unmeasured += 1
            continue
        top = r.findings[0].what if r.findings else "matches the conventions that were checked"
        lines.append(f"- **{r.ticket_key}** {_pct(r.alignment)} - {top}")
    if unmeasured:
        lines += [
            "",
            f"{unmeasured} ticket(s) could not be measured at all - the exemplar "
            "tickets share no convention consistent enough to check against.",
        ]
    lines.append("")
    return "\n".join(lines)
