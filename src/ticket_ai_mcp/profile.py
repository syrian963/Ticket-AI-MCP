# SPDX-License-Identifier: MIT

"""What this team's tickets actually look like, as numbers.

A profile is the whole point of the tool. Generic ticket advice - "add
acceptance criteria", "include steps to reproduce" - is free, ignorable and
frequently wrong for a given team. A profile replaces it with something a
reviewer cannot wave away: *thirty-one of your last forty shipped tickets have
an Akzeptanzkriterien section. This one does not.*

So every field here is a count or a rate over a named sample, and
`exemplar_keys` records exactly which tickets produced it. If someone disputes
a number, they can open the tickets it came from.

Three things this deliberately does not do:

- **Merge headings across languages.** See `textstats.normalise_heading`.
- **Invent a threshold.** A profile reports rates. Deciding that 0.6 is high
  enough to demand of a new ticket is `review.py`'s job, and it is one setting
  in one place.
- **Hide a thin sample.** Eleven exemplars produce a profile with eleven
  exemplars' worth of authority, and `notes` says so in words that end up in
  the report.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from .mining import Exemplar
from .textstats import language, median, normalise_heading, quantile, shape

# Below this, rates are noise dressed up as measurement: one ticket in a sample
# of six moves a rate by 17 points.
THIN_SAMPLE = 12

# A leading marker some teams put on every title: `fix:`, `[Backoffice]`,
# `BUG -`. Captured as a shape rather than a word so it generalises.
_TITLE_PREFIX = re.compile(r"^\s*(\[[^\]]{1,24}\]|[A-Za-zÄÖÜäöü]{2,12}\s*[:/-])\s*\S")


# A conditional convention needs a trigger that is not itself a rarity, or the
# "rule" is three tickets agreeing with each other.
MIN_TRIGGER = 5
# And the conditional rate has to beat the overall rate by enough that it is
# telling you something the overall rate did not.
MIN_LIFT = 0.25


@dataclass(frozen=True, slots=True)
class Section:
    """One recurring heading, how much of the corpus uses it, and where.

    `position` is the mean index of the heading within the tickets that have
    it, and it exists because frequency is the wrong thing to order a template
    by. On a real board `Abnahme` appears more often than `Ziel`, so a
    skeleton sorted by count put acceptance criteria first - and a model asked
    to fill that in obediently wrote the summary under `Abnahme` and the criteria
    under `Ziel`. Tickets there are written the other way round, and the
    template has to read the way the tickets read.
    """

    heading: str
    key: str
    count: int
    rate: float
    position: float = 0.0


@dataclass(frozen=True, slots=True)
class Conditional:
    """A section that is only conventional on *some* tickets.

    This exists because a single board-wide rate can hide a real convention.
    On the project this was built against, an `Abnahme` section showed up in 41% of
    tickets - far too low to hold anyone to. But among tickets that had a
    `Ziel` section it was 76%, and among those that did not, 20%. The
    team has a template; they apply it to feature work and not to bugs, and
    averaging the two says they have no template at all.

    `lift` is the part that matters: a conditional rate that merely matches the
    baseline is not a rule, it is the baseline seen through a smaller window.
    """

    when: str
    when_heading: str
    then: str
    then_heading: str
    count: int
    of: int
    rate: float
    baseline: float

    @property
    def lift(self) -> float:
        return round(self.rate - self.baseline, 3)


@dataclass(frozen=True, slots=True)
class Profile:
    project: str
    tracker: str
    built_at: str
    sample_size: int
    exemplar_keys: tuple[str, ...]

    sections: tuple[Section, ...] = ()
    conditionals: tuple[Conditional, ...] = ()
    chars_median: float = 0.0
    chars_p25: float = 0.0
    chars_p75: float = 0.0
    title_words_median: float = 0.0
    title_prefixes: tuple[tuple[str, float], ...] = ()

    label_rate: float = 0.0
    labels_per_ticket_median: float = 0.0
    common_labels: tuple[tuple[str, float], ...] = ()
    label_groups: tuple[str, ...] = ()

    list_rate: float = 0.0
    checkbox_rate: float = 0.0
    code_rate: float = 0.0
    image_rate: float = 0.0
    link_rate: float = 0.0
    cross_ref_rate: float = 0.0
    assignee_rate: float = 0.0

    language: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_thin(self) -> bool:
        return self.sample_size < THIN_SAMPLE

    def with_language(self, language: str | None) -> Profile:
        """The same profile, told what language tickets should be in.

        Applied when the profile is read rather than when it is built, so
        changing the setting takes effect immediately instead of requiring a
        two-minute re-mine of the tracker.

        Worth knowing before setting it: forcing a language the board does not
        use makes every existing ticket fail the language check. That is the
        correct answer for a team mid-switch and an annoying one for a team
        that mistyped, so the value is echoed wherever it is used.
        """
        if not language or language == self.language:
            return self
        return replace(self, language=language)

    def section(self, key: str) -> Section | None:
        return next((s for s in self.sections if s.key == key), None)

    def skeleton(self, floor: float = 0.6) -> tuple[tuple[str, str], ...]:
        """The headings to offer someone about to write a ticket, and why each.

        Board-wide rates alone are not enough here, and a real project proved
        it. On that board `Abnahme` sat at 37% and `Ziel` at 27% - both under
        any usable floor - so a skeleton built from rates was empty and the
        tool said the team writes prose. They do not: those two sections go
        together on nine of the eleven tickets that have either, which is the
        template, and it was being reported next to the skeleton instead of
        inside it.

        So a section earns a place two ways: it is common across the whole
        board, or it belongs to a block whose members imply each other. The
        reason is returned alongside, because "in 78% of tickets" and "goes
        with Ziel on 9 of 11" are different claims and a writer should
        see which one they are being held to.
        """
        offered: dict[str, str] = {}
        for section in self.sections:
            if section.rate >= floor:
                offered[section.heading] = f"in {round(section.rate * 100)}%"

        for rule in self.conditionals:
            if rule.rate < floor:
                continue
            offered.setdefault(
                rule.when_heading, f"goes with {rule.then_heading} ({rule.count}/{rule.of})"
            )
            offered.setdefault(
                rule.then_heading, f"goes with {rule.when_heading} ({rule.count}/{rule.of})"
            )

        # Ordered by where each section sits inside a ticket, not by how
        # common it is. See `Section.position`: sorted by frequency, this put
        # acceptance criteria above the ziel, and a model filling in
        # that skeleton wrote each section's content under the other's
        # heading.
        where = {s.heading: s.position for s in self.sections}
        return tuple(
            (heading, why)
            for heading, why in sorted(
                offered.items(), key=lambda kv: (where.get(kv[0], 99), kv[0])
            )
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> Profile:
        data: dict[str, Any] = json.loads(text)
        data["sections"] = tuple(Section(**s) for s in data.get("sections", ()))
        data["conditionals"] = tuple(Conditional(**c) for c in data.get("conditionals", ()))
        for key in ("exemplar_keys", "label_groups", "notes"):
            data[key] = tuple(data.get(key) or ())
        for key in ("title_prefixes", "common_labels"):
            data[key] = tuple((a, b) for a, b in data.get(key) or ())
        return cls(**data)


def _conditionals(
    per_ticket: list[set[str]],
    counts: Counter[str],
    display: dict[str, Counter[str]],
    n: int,
) -> tuple[Conditional, ...]:
    """Find sections that are conventional only when another section is present.

    Every ordered pair of headings is tested, which sounds expensive and is
    not: the heading vocabulary of a real project is small, and the pair is
    kept only if the conditional rate clears `EXPECT_RATE` in `review.py` *and*
    beats the overall rate by `MIN_LIFT`.

    Both directions are tested and both can survive. "Tickets with a ziel
    also have acceptance criteria" and "tickets with acceptance criteria also
    have a ziel" are different claims about a board, and which one is
    useful depends on which half the author has already written.
    """
    found: list[Conditional] = []
    keys = [key for key, count in counts.items() if count >= MIN_TRIGGER]
    for when in keys:
        with_trigger = [t for t in per_ticket if when in t]
        for then in keys:
            if then == when:
                continue
            together = sum(1 for t in with_trigger if then in t)
            rate = together / len(with_trigger)
            baseline = counts[then] / n
            if rate - baseline < MIN_LIFT:
                continue
            found.append(
                Conditional(
                    when=when,
                    when_heading=display[when].most_common(1)[0][0],
                    then=then,
                    then_heading=display[then].most_common(1)[0][0],
                    count=together,
                    of=len(with_trigger),
                    rate=round(rate, 3),
                    baseline=round(baseline, 3),
                )
            )
    # Strongest evidence first, so a report that truncates keeps the best of it.
    found.sort(key=lambda c: (-(c.rate - c.baseline), -c.of, c.when, c.then))
    return tuple(found[:12])


def clusters(conditionals: tuple[Conditional, ...], floor: float = 0.6) -> list[list[str]]:
    """Group sections that imply each other into the blocks they really are.

    Pairwise rules are the right shape for checking one ticket and the wrong
    shape for reading. A three-section block - a bug form's version, OS and
    Python fields, say - produces six pairwise rules, and six lines saying the
    same thing is how a report stops being read.

    Only mutual pairs are merged: A implies B *and* B implies A. A one-way rule
    is a different claim and stays on its own.
    """
    mutual = {
        (c.when, c.then)
        for c in conditionals
        if c.rate >= floor
        and any(o.when == c.then and o.then == c.when and o.rate >= floor for o in conditionals)
    }
    label = {c.when: c.when_heading for c in conditionals}
    label.update({c.then: c.then_heading for c in conditionals})

    parent: dict[str, str] = {}

    def root(key: str) -> str:
        parent.setdefault(key, key)
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for left, right in mutual:
        parent[root(left)] = root(right)

    grouped: dict[str, list[str]] = {}
    for key in sorted({k for pair in mutual for k in pair}):
        grouped.setdefault(root(key), []).append(label.get(key, key))
    return [members for members in grouped.values() if len(members) > 1]


def build(exemplars: list[Exemplar], *, project: str, tracker: str) -> Profile:
    """Turn a corpus into a profile.

    An empty corpus produces an empty profile rather than an exception. The
    caller usually got here because a project has no closed tickets yet, and
    that is an answer to report, not a crash.
    """
    tickets = [e.detail.ticket for e in exemplars]
    n = len(tickets)
    notes: list[str] = []

    if n == 0:
        return Profile(
            project=project,
            tracker=tracker,
            built_at=datetime.now(UTC).isoformat(),
            sample_size=0,
            exemplar_keys=(),
            notes=("No ticket in the sample could be used as an exemplar.",),
        )

    shapes = [shape(t.description) for t in tickets]

    # Count a heading once per ticket. A template repeated in a quoted reply
    # would otherwise let one ticket vote twice.
    heading_counts: Counter[str] = Counter()
    display: dict[str, Counter[str]] = {}
    per_ticket: list[set[str]] = []
    # Where each heading tends to sit inside a ticket, so the template can be
    # ordered the way tickets are written rather than by how common each
    # section is.
    positions: dict[str, list[int]] = {}
    for s in shapes:
        keys: dict[str, str] = {}
        order: dict[str, int] = {}
        for index, raw in enumerate(s.headings):
            key = normalise_heading(raw)
            if not key:
                continue
            keys.setdefault(key, raw)
            order.setdefault(key, index)
        per_ticket.append(set(keys))
        for key, raw in keys.items():
            heading_counts[key] += 1
            display.setdefault(key, Counter())[raw.strip()] += 1
            positions.setdefault(key, []).append(order[key])

    sections = tuple(
        Section(
            heading=display[key].most_common(1)[0][0],
            key=key,
            count=count,
            rate=round(count / n, 3),
            position=round(sum(positions[key]) / len(positions[key]), 2),
        )
        # A heading that appears once is that ticket's heading, not the team's.
        for key, count in heading_counts.most_common(25)
        if count >= 2
    )

    conditionals = _conditionals(per_ticket, heading_counts, display, n)

    prefix_counts: Counter[str] = Counter()
    for t in tickets:
        match = _TITLE_PREFIX.match(t.title)
        if match:
            prefix_counts[match.group(1).strip()] += 1

    label_counts: Counter[str] = Counter()
    for t in tickets:
        label_counts.update(set(t.labels))

    groups = sorted({label.split("::", 1)[0] for label in label_counts if "::" in label})

    corpus_text = "\n".join(f"{t.title}\n{t.description}" for t in tickets)

    if n < THIN_SAMPLE:
        notes.append(
            f"Built from {n} tickets. Every rate below moves by more than "
            f"{round(100 / n)} points if one ticket changes, so treat them as a hint "
            "rather than a rule."
        )
    if not sections:
        notes.append(
            "No heading appears in two or more of these tickets: this team does not "
            "seem to use a template, so nothing here can check for one."
        )

    chars = [float(s.chars) for s in shapes]
    return Profile(
        project=project,
        tracker=tracker,
        built_at=datetime.now(UTC).isoformat(),
        sample_size=n,
        exemplar_keys=tuple(t.key for t in tickets),
        sections=sections,
        conditionals=conditionals,
        chars_median=median(chars),
        chars_p25=quantile(chars, 0.25),
        chars_p75=quantile(chars, 0.75),
        title_words_median=median([float(len(t.title.split())) for t in tickets]),
        title_prefixes=tuple(
            (prefix, round(count / n, 3)) for prefix, count in prefix_counts.most_common(5)
        ),
        label_rate=round(sum(1 for t in tickets if t.labels) / n, 3),
        labels_per_ticket_median=median([float(len(t.labels)) for t in tickets]),
        common_labels=tuple(
            (label, round(count / n, 3)) for label, count in label_counts.most_common(12)
        ),
        label_groups=tuple(groups),
        list_rate=round(sum(1 for s in shapes if s.list_items) / n, 3),
        checkbox_rate=round(sum(1 for s in shapes if s.checkboxes) / n, 3),
        code_rate=round(sum(1 for s in shapes if s.code_blocks) / n, 3),
        image_rate=round(sum(1 for s in shapes if s.images) / n, 3),
        link_rate=round(sum(1 for s in shapes if s.links) / n, 3),
        cross_ref_rate=round(sum(1 for s in shapes if s.ticket_refs) / n, 3),
        assignee_rate=round(sum(1 for t in tickets if t.assignees) / n, 3),
        language=language(corpus_text),
        notes=tuple(notes),
    )
