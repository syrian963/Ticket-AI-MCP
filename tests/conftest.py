# SPDX-License-Identifier: MIT

"""Builders for tickets, so a test says what it is about and nothing else.

Every field of a Ticket is required, and a test that has to spell out eleven of
them to make a point about one is a test nobody reads.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ticket_ai_mcp.schemas import Comment, LinkedChange, Ticket, TicketDetail

BASE = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def make_ticket(
    key: str = "#1",
    *,
    title: str = "Fix the label printing",
    description: str = "",
    state: str = "closed",
    labels: tuple[str, ...] = (),
    author: str = "mira",
    assignees: tuple[str, ...] = ("mouhamad",),
    age_hours: float = 72.0,
    project: str = "acme/shop",
) -> Ticket:
    created = BASE
    closed = created + timedelta(hours=age_hours) if state == "closed" else None
    return Ticket(
        uid=f"gitlab:{project}{key}",
        key=key,
        title=title,
        description=description,
        state=state,
        labels=labels,
        author=author,
        assignees=assignees,
        created_at=created,
        updated_at=closed or created,
        closed_at=closed,
        url=f"https://git.example.com/{project}/-/issues/{key.lstrip('#')}",
        tracker="gitlab",
        project=project,
    )


def make_detail(
    ticket: Ticket | None = None,
    *,
    merged: bool = True,
    questions: int = 0,
    reopens: int = 0,
    linked: tuple[LinkedChange, ...] | None = None,
    **kwargs,
) -> TicketDetail:
    """A ticket plus the history that mining reads.

    `questions` is the knob that matters most: it is the count of human
    comments containing a question mark, which is how mining detects a ticket
    that was not clear as written.

    `linked` overrides the changes entirely, for the case `merged` cannot
    express: a ticket that links to a change whose merge state the tracker
    does not report. That is every Jira ticket, and it is the difference
    between "nothing was linked" and "nothing is known".
    """
    ticket = ticket or make_ticket(**kwargs)
    comments = tuple(
        Comment(author="dev", body=f"what does {i} mean?", created_at=BASE)
        for i in range(questions)
    )
    if linked is not None:
        changes = linked
    else:
        changes = (
            (LinkedChange(ref="!9", title="do it", url="u", state="merged", merged=True),)
            if merged
            else ()
        )
    return TicketDetail(
        ticket=ticket, comments=comments, linked_changes=changes, reopen_count=reopens
    )


GOOD_BODY = """## Problem
The CSV export drops the last row when the result set is a multiple of 100.

## Steps to reproduce
1. Open the customer list
2. Filter to exactly 200 customers
3. Export as CSV

## Acceptance criteria
- [ ] The export contains every filtered row
- [ ] A regression test covers the boundary
"""


@pytest.fixture
def good_body() -> str:
    return GOOD_BODY


@pytest.fixture(autouse=True)
def _registry_stays_put():
    """No test may leave an adapter behind for the next one.

    The tracker registry is module state, and a fake adapter registered for
    one test was visible to every test that ran afterwards - which is how a
    check on "which trackers exist" passed alone and failed in the suite.
    """
    from ticket_ai_mcp.trackers.base import _REGISTRY

    before = dict(_REGISTRY)
    yield
    _REGISTRY.clear()
    _REGISTRY.update(before)
