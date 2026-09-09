# SPDX-License-Identifier: MIT

"""The vocabulary every other module speaks.

One deliberate choice runs through this file: a tracker-shaped record is
normalised once, here, and nothing downstream is allowed to care whether the
ticket came from GitLab, Jira or GitHub. `raw` is the escape hatch for the
handful of places that genuinely need a native field, and reaching for it is a
signal that something belongs in this file instead.

Times are always timezone-aware UTC. A tracker that hands back a naive string
gets it fixed at the adapter boundary, not here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class Comment:
    """One note on a ticket.

    `system` marks the notes a tracker writes about itself - "changed status to
    closed", "mentioned in merge request !42". They are worthless as prose and
    invaluable as evidence, so they are kept and flagged rather than dropped.
    """

    author: str
    body: str
    created_at: datetime
    system: bool = False


@dataclass(frozen=True, slots=True)
class LinkedChange:
    """A merge request or pull request that claims to implement a ticket."""

    ref: str
    title: str
    url: str
    state: str
    merged: bool


@dataclass(frozen=True, slots=True)
class Ticket:
    """A ticket, flattened to the fields every tracker actually has.

    `key` is what a human types and what shows up in a commit message - `#42`,
    `PROJ-123`. `uid` is what this tool stores, and it carries the tracker and
    project so two sources cannot collide inside one corpus.
    """

    uid: str
    key: str
    title: str
    description: str
    state: str
    labels: tuple[str, ...]
    author: str
    assignees: tuple[str, ...]
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    url: str
    tracker: str
    project: str
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def is_closed(self) -> bool:
        return self.state == "closed"


@dataclass(frozen=True, slots=True)
class TicketDetail:
    """A ticket plus the history that says whether it was any good.

    Fetching this costs one or two more API calls per ticket, and that cost is
    the only reason the ranking in `mining.py` works at all. A title and a
    description cannot tell you that a ticket took eleven rounds of "what do
    you mean?" before anyone could start on it.
    """

    ticket: Ticket
    comments: tuple[Comment, ...] = ()
    linked_changes: tuple[LinkedChange, ...] = ()
    reopen_count: int = 0

    @property
    def human_comments(self) -> tuple[Comment, ...]:
        return tuple(c for c in self.comments if not c.system)

    @property
    def was_implemented(self) -> bool:
        """Did code actually ship for this ticket?

        A merged change is the strongest evidence a tracker offers that a
        ticket was understood well enough to act on.
        """
        return any(c.merged for c in self.linked_changes)


@dataclass(frozen=True, slots=True)
class TicketQuery:
    """What to ask a tracker for.

    Kept small on purpose. Every field here has to be expressible in GitLab
    REST, Jira JQL and the GitHub search API, or it does not belong.
    """

    project: str
    state: str | None = None
    labels: tuple[str, ...] = ()
    updated_after: datetime | None = None
    text: str | None = None
    limit: int = 100
