# SPDX-License-Identifier: MIT

"""What separates the tickets that shipped from the ones that did not.

Everything else here measures the tickets that worked. This measures the
others too, and reports the difference - which is a strictly stronger claim.

    "85% of shipped tickets have Steps to reproduce"
    "85% of shipped tickets have it, and 30% of the ones that stalled"

The first is a rate. The second is evidence that the section is doing
something. A team can shrug at the first and has to argue with the second.

**The split is on outcome alone, and that is the whole design.** A ticket lands
in one group or the other because of what happened to it - a merged change, a
reopen, a run of clarifying questions - and never because of what it contains.
Splitting on content and then comparing content would be circular: sections
would "predict" success because having sections is what put the ticket in the
successful group. The two halves of this file are deliberately kept apart for
that reason, and it is the one thing not to change without thinking hard.

Two guards keep the numbers honest:

- **Both groups have to be big enough.** A "signal" over four stalled tickets
  is one ticket's opinion with a percent sign on it.
- **The gap has to be wide enough.** Rates over samples this size move by ten
  points on noise alone, so a five-point difference is not a finding.
"""

from __future__ import annotations

from dataclasses import dataclass

from .mining import _BOT, Exemplar, score
from .schemas import TicketDetail
from .textstats import normalise_heading, shape

# Below this in either group, a rate is noise. Ten is already thin; it is the
# floor at which one ticket moves a rate by ten points rather than twenty.
MIN_GROUP = 8

# How far apart the two rates have to be before the difference is worth
# reporting rather than explaining away.
MIN_GAP = 0.25

# More than this many clarifying questions and the ticket was not clear as
# written, whatever else happened to it.
NOISY_QUESTIONS = 4


@dataclass(frozen=True, slots=True)
class Signal:
    """One feature, measured in both groups."""

    key: str
    label: str
    shipped_rate: float
    stalled_rate: float
    shipped_count: int
    stalled_count: int

    @property
    def gap(self) -> float:
        return round(self.shipped_rate - self.stalled_rate, 3)

    @property
    def helps(self) -> bool:
        """Present in the ones that shipped, absent in the ones that did not."""
        return self.gap > 0


@dataclass(frozen=True, slots=True)
class Contrast:
    shipped: int = 0
    stalled: int = 0
    signals: tuple[Signal, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        return self.shipped >= MIN_GROUP and self.stalled >= MIN_GROUP

    def signal(self, key: str) -> Signal | None:
        return next((s for s in self.signals if s.key == key), None)


def closed_by_robot(detail: TicketDetail) -> bool:
    """Was this closed by a staleness bot rather than by a decision?

    Found on home-assistant/core, where the comparison came back saying that
    every field of their mandatory issue form was *more* common among the
    tickets that stalled. That is not a finding about writing. Their bot closes
    anything quiet for long enough, so "stalled" had filled up with
    well-written tickets whose only fault was age - and since the form is
    mandatory, they all carried every field.

    A stale-closed ticket is evidence about attention, not about the ticket, so
    it belongs in neither group.
    """
    # Written out rather than as one chained expression: the precedence of
    # `and` against `or` here is exactly the kind of thing that reads correct
    # and is not.
    #
    # The phrasings are the ones bots actually use, and there are more of them
    # than you would guess - "closed as inactive", "closing as inactive",
    # "marking stale", "no activity ... closing". A first pass matched only the
    # past tense and missed half of them.
    for comment in detail.comments:
        text = comment.body.lower()
        closing = any(w in text for w in ("closed", "closing", "close ", "inactivity"))
        if "stale" in text and closing:
            return True
        if "inactive" in text and closing:
            return True
        if "no activity" in text and closing:
            return True
    return any(_BOT.search(c.author) for c in detail.comments if "stale" in c.body.lower())


def outcome(detail: TicketDetail) -> str:
    """Which group a ticket belongs to, from what happened to it.

    Deliberately blind to the description. It reads only the tracker's record
    of how the ticket went:

    - **shipped** - a change was merged for it, nobody reopened it, and it did
      not draw a run of clarifying questions.
    - **stalled** - closed with nothing merged, or reopened, or it took more
      than a handful of "what do you mean?" comments before it moved.
    - **unclear** - closed with a merged change but reopened once, say, or
      closed by a staleness bot. Real, and not evidence either way, so it is
      left out rather than forced into a group to make the numbers bigger.
    """
    if not detail.ticket.is_closed:
        return "unclear"
    if closed_by_robot(detail):
        return "unclear"

    questions = sum(1 for c in detail.human_comments if "?" in c.body)
    if detail.was_implemented and detail.reopen_count == 0 and questions <= 1:
        return "shipped"
    if not detail.was_implemented or detail.reopen_count >= 2 or questions > NOISY_QUESTIONS:
        return "stalled"
    return "unclear"


def _features(detail: TicketDetail) -> dict[str, tuple[str, bool]]:
    """Everything about a ticket's content worth comparing, as yes/no.

    Rates over yes/no features are comparable between two groups of different
    sizes, which averages are not: a group whose descriptions are twice as long
    tells you nothing until you know whether that is one outlier.
    """
    ticket = detail.ticket
    body = shape(ticket.description)
    out: dict[str, tuple[str, bool]] = {
        "has_labels": ("carries a label", bool(ticket.labels)),
        "has_assignee": ("is assigned", bool(ticket.assignees)),
        "has_list": ("uses a list", body.list_items > 0),
        "has_checklist": ("uses a checklist", body.checkboxes > 0),
        "has_code": ("includes a code block", body.code_blocks > 0),
        "has_image": ("includes a screenshot", body.images > 0),
        "has_link": ("links out", body.links > 0),
        "has_cross_ref": ("links another ticket", bool(body.ticket_refs)),
        "has_any_heading": ("has any section at all", bool(body.headings)),
    }
    # Length is the one continuous feature worth keeping, so it is turned into
    # a question with an answer: is this description a substantial one?
    out["substantial"] = ("runs to 400 characters or more", body.chars >= 400)
    for raw in body.headings:
        key = normalise_heading(raw)
        if key:
            out[f"section:{key}"] = (raw.strip(), True)
    return out


def build(details: list[TicketDetail]) -> Contrast:
    """Measure both groups and report where they differ.

    An unusable contrast is returned rather than raised: a board where almost
    everything shipped is a good board, and the honest output is "nothing to
    compare against", not an error.
    """
    notes: list[str] = []

    # "Nothing was merged for this ticket" is the main reason a ticket lands in
    # the stalled group, and it is only evidence if a merged change is visible
    # at all. On a corpus where not one ticket has one, it is not: Jira has no
    # supported API for the development panel, so a team that does not post
    # remote links looks, to this code, like a team that never shipped
    # anything. Seven public Jira boards measured: 349 tickets, 349 stalled,
    # zero shipped. The floor below stopped any advice coming out of that, but
    # the numbers themselves were still shown to a reader, and "0 of 50 of your
    # tickets shipped" is a false thing to tell a team about its own board.
    #
    # The test is on a *merged* change and not on a link, which took a second
    # measurement to get right: Apache's KAFKA board has a remote link on 32 of
    # 33 tickets and Jira reports the merge state of exactly none of them, so a
    # guard that asked whether links exist passed and left the split as wrong
    # as it was.
    if details and not any(d.was_implemented for d in details):
        notes.append(
            "This tracker did not report a merged change for a single ticket, so "
            "there is no way to tell the ones that got built from the ones that "
            "did not. On Jira this is usual: there is no supported API for the "
            "development panel, and a remote link does not say whether what it "
            "points at was merged. Nothing here is compared; every number in the "
            "profile still stands on its own."
        )
        return Contrast(shipped=0, stalled=0, notes=tuple(notes))

    groups: dict[str, list[TicketDetail]] = {"shipped": [], "stalled": []}
    for detail in details:
        group = outcome(detail)
        if group in groups:
            groups[group].append(detail)

    shipped, stalled = groups["shipped"], groups["stalled"]

    if len(shipped) < MIN_GROUP or len(stalled) < MIN_GROUP:
        which = "shipped cleanly" if len(shipped) < MIN_GROUP else "stalled"
        notes.append(
            f"Only {min(len(shipped), len(stalled))} tickets {which}, so there is "
            f"nothing to compare against - {MIN_GROUP} is the floor at which a rate "
            "means anything. Every number in the profile still stands on its own."
        )
        return Contrast(shipped=len(shipped), stalled=len(stalled), notes=tuple(notes))

    # Every feature seen in either group, so a section that only the stalled
    # ones use is visible too.
    labels: dict[str, str] = {}
    seen: dict[str, dict[str, int]] = {}
    for group, members in (("shipped", shipped), ("stalled", stalled)):
        for detail in members:
            for key, (label, present) in _features(detail).items():
                labels.setdefault(key, label)
                counts = seen.setdefault(key, {"shipped": 0, "stalled": 0})
                if present:
                    counts[group] += 1

    signals: list[Signal] = []
    for key, counts in seen.items():
        shipped_rate = counts["shipped"] / len(shipped)
        stalled_rate = counts["stalled"] / len(stalled)
        if abs(shipped_rate - stalled_rate) < MIN_GAP:
            continue
        signals.append(
            Signal(
                key=key,
                label=labels[key],
                shipped_rate=round(shipped_rate, 3),
                stalled_rate=round(stalled_rate, 3),
                shipped_count=counts["shipped"],
                stalled_count=counts["stalled"],
            )
        )

    signals.sort(key=lambda s: (-abs(s.gap), s.key))
    if not signals:
        notes.append(
            "Nothing in the descriptions separates the two groups. On this board "
            "whether a ticket got built is not predicted by how it was written, "
            "which is worth knowing before anyone is asked to write differently."
        )
    return Contrast(
        shipped=len(shipped),
        stalled=len(stalled),
        signals=tuple(signals),
        notes=tuple(notes),
    )


def exemplars_and_contrast(details: list[TicketDetail]) -> tuple[list[Exemplar], Contrast]:
    """Score every ticket once, and reuse the pass for both jobs.

    `mining.pick` scores to choose a corpus; this scores to compare outcomes.
    Doing both from one walk keeps the two from disagreeing about a ticket.
    """
    return [score(d) for d in details], build(details)
