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
  exemplars worth of authority, and the notes say so in words that end up in
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

# How many headings may be used as the "when" half of a rule.
#
# Every ordered pair of triggers is examined, so the work grows with the square
# of this number and nothing was bounding it. Measured with a corpus of twelve
# tickets: 500 recurring headings cost 0.3 seconds, 2000 cost 3.5, and 3000
# cost ten - and `learn_conventions` is an MCP call somebody is waiting on.
#
# The widest real board in the fleet produced eleven triggers, and the one with
# the most sections had twenty-five. Sixty is five times the worst case
# observed and turns an unbounded quadratic into 3540 pairs. When the cap
# binds, it keeps the headings the team uses most, because a rule wants
# evidence behind it.
MAX_TRIGGERS = 60

# A stop, not a target. Every rule that clears the threshold is kept, because
# `clusters` needs the whole graph to recognise a block; the report shows the
# strongest handful. The widest board in the fleet, BurntSushi/ripgrep,
# produces fifty-six - which the report prints as one block of eight sections
# and no loose rules at all, and which costs a reviewed ticket at most one
# finding. Keeping the graph whole makes the output smaller, not larger.
MAX_CONDITIONALS = 90

# A leading marker some teams put on every title: `fix:`, `[Backoffice]`,
# `BUG -`. Captured as a shape rather than a word so it generalises.
_TITLE_PREFIX = re.compile(r"^\s*(\[[^\]]{1,24}\]|[A-Za-zÄÖÜäöü]{2,12}\s*[:/-])\s*\S")


# A conditional convention needs a trigger that is not itself a rarity, or the
# "rule" is three tickets agreeing with each other. Raised from five after
# measurement: at five, a trigger is one ticket away from moving its rate by
# twenty points.
MIN_TRIGGER = 8

# And the conditional rate has to beat the overall rate by enough that it is
# telling you something the overall rate did not.
#
# **This is a floor, not the test.** Every ordered pair of sections is a
# hypothesis, so a board with fourteen sections tests around 180 of them, and
# keeping whichever look good is the oldest mistake in statistics. Simulated
# against random data: a pair with no association at all clears a flat 0.25
# lift 8% of the time, which is fourteen invented rules per board - and it
# showed, because sixteen of thirty boards came back holding exactly the
# twelve the display cap allowed.
#
# `required_lift` below raises the bar with the number of pairs tested.
MIN_LIFT = 0.25


def required_lift(pairs: int) -> float:
    """How big a gap has to be, given how many pairs were examined.

    Testing more hypotheses means seeing more extremes by luck, so the
    threshold rises with the count. The shape is deliberately crude - there is
    no distributional claim here worth a real correction - but it moves in the
    right direction and it is honest about why: on a board with two sections a
    0.25 gap is interesting, and on a board with twenty it is noise.
    """
    if pairs <= 6:
        return MIN_LIFT
    # +0.05 for every doubling of the pair count past six, capped so a very
    # wide board does not become unanswerable.
    import math

    steps = math.log2(pairs / 6)
    return min(0.55, MIN_LIFT + 0.05 * steps)


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
    # Sections that separated the tickets that shipped from the ones that
    # stalled, as {normalised key: (shipped rate, stalled rate)}. Stored on the
    # profile so a review can quote the contrast without re-mining, and empty
    # whenever the two groups were too small to compare. See `contrast.py`.
    discriminative: dict[str, tuple[float, float]] = field(default_factory=dict)
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
    # The caveats, stored as a code and its measurements rather than as a
    # sentence - the same move `Finding` made, for the same reason. A note
    # built as an English f-string here is an English sentence on a German
    # page, and these are the sentences that decide how much of the rest of
    # the page a reader should believe. `notes` renders them in English;
    # `localised_notes` renders them in whichever language was asked for.
    note_codes: tuple[tuple[str, dict[str, Any]], ...] = field(default_factory=tuple)

    @property
    def notes(self) -> tuple[str, ...]:
        return self.localised_notes()

    def localised_notes(self, language: str | None = None) -> tuple[str, ...]:
        from .messages import render_note

        return tuple(render_note(code, params, language) for code, params in self.note_codes)

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
        for key in ("exemplar_keys", "label_groups"):
            data[key] = tuple(data.get(key) or ())
        # A note is a pair, and JSON has neither tuples nor a memory of which
        # list was meant to be one.
        data["note_codes"] = tuple(
            (code, dict(params)) for code, params in data.get("note_codes") or ()
        )
        # A profile cached before notes became codes carries finished English
        # sentences under the old key. Loading it has to keep working - the
        # cache is written next to somebody's checkout and they did not ask for
        # it to expire - and the sentences have to survive, because dropping a
        # caveat quietly is the thing this whole change was about.
        legacy = data.pop("notes", None)
        if legacy and not data["note_codes"]:
            data["note_codes"] = tuple(("_literal", {"text": text}) for text in legacy)
        for key in ("title_prefixes", "common_labels"):
            data[key] = tuple((a, b) for a, b in data.get(key) or ())
        # JSON has no tuples; the pairs come back as lists.
        data["discriminative"] = {
            k: (float(v[0]), float(v[1])) for k, v in (data.get("discriminative") or {}).items()
        }
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
    common = [key for key, _ in counts.most_common() if counts[key] >= MIN_TRIGGER]
    keys = common[:MAX_TRIGGERS]
    # The bar rises with the number of pairs examined. See `required_lift`:
    # a flat threshold across 180 pairs invents about fourteen rules a board.
    threshold = required_lift(len(keys) * max(len(keys) - 1, 0))
    for when in keys:
        with_trigger = [t for t in per_ticket if when in t]
        for then in keys:
            if then == when:
                continue
            together = sum(1 for t in with_trigger if then in t)
            rate = together / len(with_trigger)
            baseline = counts[then] / n
            if rate - baseline < threshold:
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

    # This used to cut at twelve, and twelve turned out to be doing two things
    # wrong. Measured across five boards: on rollup/rollup thirty rules cleared
    # the threshold and the cut fell in the middle of a tie - eighteen dropped,
    # the best of them with exactly the lift of the weakest kept. There is no
    # ranking inside a tie, so which twelve survived was arbitrary.
    #
    # Worse, those thirty rules were every ordered pair of six sections: a board
    # whose template is one rigid block. `clusters` finds a block by looking for
    # pairs that hold in both directions, so truncating first handed it a graph
    # with half its edges missing and it reported the block as loose rules.
    # Truncation is a display concern and it now lives in the report.
    #
    # The ceiling that remains is a sanity stop, not a selection: a vocabulary
    # wide enough to clear it is a bug worth noticing, not a house style.
    return tuple(found[:MAX_CONDITIONALS])


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


def build(
    exemplars: list[Exemplar],
    *,
    project: str,
    tracker: str,
    contrast=None,
    limits: tuple[tuple[str, dict[str, Any]], ...] = (),
) -> Profile:
    """Turn a corpus into a profile.

    An empty corpus produces an empty profile rather than an exception. The
    caller usually got here because a project has no closed tickets yet, and
    that is an answer to report, not a crash.

    `contrast` is optional and carries the shipped-against-stalled comparison
    from `contrast.py`. When it is present, findings can quote both rates
    instead of one, which turns "78% of tickets have this" into "78% of the
    ones that shipped, and 30% of the ones that stalled".

    `limits` is what the tracker would not serve. It becomes a note rather than
    a separate field because a note travels: the profile is cached and read
    back for every later review, and a profile built from a board that was only
    half readable has to say so every time it is shown, not only in the run
    that built it.
    """
    tickets = [e.detail.ticket for e in exemplars]
    n = len(tickets)
    notes: list[tuple[str, dict[str, Any]]] = list(limits)

    if n == 0:
        return Profile(
            project=project,
            tracker=tracker,
            built_at=datetime.now(UTC).isoformat(),
            sample_size=0,
            exemplar_keys=(),
            note_codes=(*limits, ("no_exemplars", {})),
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
        notes.append(("thin_sample", {"n": n, "points": round(100 / n)}))
    if not sections:
        notes.append(("no_template", {}))

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
        # Only the section signals: the habit ones are already reported as
        # rates, and a review quotes the contrast when it is checking a
        # section it can name.
        discriminative={
            s.key.removeprefix("section:"): (s.shipped_rate, s.stalled_rate)
            for s in (contrast.signals if contrast and contrast.usable else ())
            if s.key.startswith("section:")
        },
        note_codes=tuple(notes),
    )
