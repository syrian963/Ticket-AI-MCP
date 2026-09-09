# SPDX-License-Identifier: MIT

"""Finding the tickets worth learning from, when nobody hands you any.

The question this file answers is narrower than it looks. It is **not** "which
tickets are good" - that is a judgement about content, and this tool does not
read content. It is "which closed tickets are the safest evidence of how this
team writes a ticket that someone could act on".

The difference matters, because the signals here are all circumstantial:

- A ticket with a merged change attached was understood well enough to build.
- A ticket nobody had to reopen was understood well enough to finish.
- A ticket that drew eleven questions before work started was not clear,
  whatever its description looks like.

None of that proves quality. A perfectly written ticket can be closed as
out-of-scope with no MR, and it will score badly here. That is an acceptable
error for the job: we need thirty *representative* tickets, not the thirty best
ones, and the cost of missing a good one is nothing.

Every score carries the reasons that produced it, because a corpus a team
cannot argue with is a corpus they will not trust.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta

from .schemas import TicketDetail
from .textstats import shape

# Bots write a great many tickets and none of them are written the way this
# team writes tickets. Learning a house style from Renovate is a real failure
# mode, not a hypothetical one.
_BOT = re.compile(r"(\[bot\]|(^|[-_.])bot$|dependabot|renovate|snyk|gitlab-ci|jenkins)", re.I)

# Below this, a description carries no structure to learn from. It is not a
# claim that a 60-character ticket is bad - some are perfect - only that it
# teaches nothing about a template.
MIN_DESCRIPTION_CHARS = 80


@dataclass(frozen=True, slots=True)
class Exemplar:
    """One candidate, with the arithmetic that put it where it is."""

    detail: TicketDetail
    score: float
    reasons: tuple[str, ...]

    @property
    def key(self) -> str:
        return self.detail.ticket.key


@dataclass(frozen=True, slots=True)
class Rejected:
    """A candidate that was excluded outright, and why.

    Kept and returned rather than silently dropped: "we mined 200 tickets and
    used 31" invites the obvious question, and this answers it.
    """

    key: str
    reason: str


def _excluded(detail: TicketDetail) -> str | None:
    ticket = detail.ticket
    if _BOT.search(ticket.author):
        return f"written by {ticket.author}, which looks like a bot"
    if not ticket.is_closed:
        return "still open, so there is no evidence it was actionable"
    if shape(ticket.description).chars < MIN_DESCRIPTION_CHARS:
        return f"description is under {MIN_DESCRIPTION_CHARS} characters"
    return None


def score(detail: TicketDetail) -> Exemplar:
    """Rate one closed ticket as evidence of house style.

    The weights below are a starting position, not a discovery. They add up to
    one, they are all visible in one place, and they are meant to be argued
    with by anyone whose tracker works differently.
    """
    ticket = detail.ticket
    body = shape(ticket.description)
    points = 0.0
    reasons: list[str] = []

    # Shipped code is the strongest evidence available that someone could act
    # on the ticket as written.
    if detail.was_implemented:
        points += 0.30
        reasons.append("a merge request for it was merged")
    elif detail.linked_changes:
        points += 0.12
        reasons.append("a change was linked but not merged")
    else:
        reasons.append("no linked change, so nothing proves it was actionable")

    if detail.reopen_count == 0:
        points += 0.10
    else:
        reasons.append(f"reopened {detail.reopen_count}x, so it was closed before it was done")

    # Length, scored against a fixed floor rather than the corpus: this runs
    # before there is a corpus to compare against.
    if body.chars >= 400:
        points += 0.20
        reasons.append(f"{body.chars} characters of description")
    elif body.chars >= 200:
        points += 0.12
    else:
        points += 0.04

    if body.headings:
        points += 0.15
        reasons.append(f"{len(body.headings)} sections: {', '.join(body.headings[:4])}")
    elif body.list_items >= 2:
        points += 0.08
        reasons.append(f"{body.list_items} list items but no headings")

    questions = sum(1 for c in detail.human_comments if "?" in c.body)
    if questions == 0:
        points += 0.15
        reasons.append("nobody had to ask a clarifying question")
    elif questions <= 2:
        points += 0.09
    elif questions <= 5:
        points += 0.03
        reasons.append(f"{questions} comments asked a question before it moved")
    else:
        reasons.append(f"{questions} comments asked a question, so it was not clear as written")

    if ticket.labels:
        points += 0.10
        reasons.append(f"labelled {', '.join(ticket.labels[:4])}")

    # A ticket opened and closed inside an hour is usually a duplicate, a typo
    # fix or housekeeping. Its shape is not the shape of real work.
    if ticket.closed_at and ticket.created_at:
        lifetime = ticket.closed_at - ticket.created_at
        if lifetime < timedelta(hours=1):
            points *= 0.5
            reasons.append("opened and closed within the hour, so probably not real work")

    return Exemplar(detail=detail, score=round(min(points, 1.0), 3), reasons=tuple(reasons))


def _kind(ticket, popularity: Counter[str]) -> str:
    """The label that best says what sort of ticket this is.

    A ticket carries several labels and their order means nothing, so the most
    widely used one is taken as its kind: on a board where `Bug` is on half the
    tickets, a ticket labelled `Bug, Frontend` is a bug that touches the
    frontend rather than a frontend ticket that happens to be broken.
    """
    if not ticket.labels:
        return ""
    return max(ticket.labels, key=lambda label: (popularity[label], label))


def pick(
    details: list[TicketDetail],
    *,
    want: int = 30,
    max_share_per_author: float = 0.4,
) -> tuple[list[Exemplar], list[Rejected]]:
    """Choose a corpus from a pile of closed tickets.

    Ranking alone gets this wrong twice, and both were found by running it
    against a real project rather than reasoned about in advance.

    **One person's voice.** On most teams one or two people write most of the
    tickets, so the top thirty by score can be twenty-four by the same person,
    and the profile then describes them rather than the team.
    `max_share_per_author` caps that.

    **One kind of ticket.** This one is worse, because it is the scoring's own
    fault. A merged merge request is weighted heavily, and small bug tickets
    attract clean merge requests far more reliably than feature work does. On
    a real board where 42% of tickets used the team's template, ranking alone
    returned a corpus where only 30% did - it had quietly selected bugs and
    thrown away the template that feature tickets follow.

    So the corpus is stratified: each kind of ticket gets a share of the slots
    matching its share of the candidate pool, and ranking only decides *which*
    tickets fill that kind's slots. The corpus mirrors the board, and the
    ranking no longer gets to pick the subject matter.
    """
    rejected: list[Rejected] = []
    scored: list[Exemplar] = []
    seen: set[str] = set()
    for detail in details:
        # A ticket that arrives twice votes twice on every rate in the profile.
        # A paging bug in an adapter put two duplicates into a real corpus and
        # nothing downstream noticed, so the guard lives here as well as in the
        # adapter that caused it.
        if detail.ticket.uid in seen:
            continue
        seen.add(detail.ticket.uid)
        reason = _excluded(detail)
        if reason:
            rejected.append(Rejected(key=detail.ticket.key, reason=reason))
            continue
        scored.append(score(detail))

    scored.sort(key=lambda e: (-e.score, e.detail.ticket.key))
    if len(scored) <= want:
        return scored, rejected

    popularity: Counter[str] = Counter()
    for candidate in scored:
        popularity.update(candidate.detail.ticket.labels)

    groups: dict[str, list[Exemplar]] = {}
    for candidate in scored:
        groups.setdefault(_kind(candidate.detail.ticket, popularity), []).append(candidate)

    # Largest-remainder allocation, so the slots add up to `want` exactly
    # instead of drifting by a couple through rounding.
    total = len(scored)
    exact = {kind: len(members) * want / total for kind, members in groups.items()}
    quota = {kind: int(share) for kind, share in exact.items()}
    for kind in sorted(exact, key=lambda k: (-(exact[k] - quota[k]), k)):
        if sum(quota.values()) >= want:
            break
        quota[kind] += 1

    author_cap = max(1, int(want * max_share_per_author))
    taken: list[Exemplar] = []
    overflow: list[Exemplar] = []
    per_author: dict[str, int] = {}

    for kind, members in groups.items():
        slots = quota.get(kind, 0)
        for candidate in members:
            author = candidate.detail.ticket.author
            if slots <= 0 or per_author.get(author, 0) >= author_cap:
                overflow.append(candidate)
                continue
            taken.append(candidate)
            per_author[author] = per_author.get(author, 0) + 1
            slots -= 1

    # Whatever the caps cost us, take back by rank. A thin corpus is a worse
    # problem than a lopsided one.
    if len(taken) < want:
        overflow.sort(key=lambda e: (-e.score, e.detail.ticket.key))
        taken.extend(overflow[: want - len(taken)])

    taken.sort(key=lambda e: (-e.score, e.detail.ticket.key))
    return taken, rejected
