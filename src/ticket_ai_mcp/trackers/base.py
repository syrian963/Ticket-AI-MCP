# SPDX-License-Identifier: MIT

"""The contract a tracker has to satisfy, and the little that is shared.

Three methods. That is the whole surface, and keeping it that small is what
makes a fourth tracker a day of work instead of a rewrite:

- `search` answers "which tickets match this", cheaply, in bulk.
- `fetch` answers "give me this one ticket".
- `detail` answers "and what happened to it" - comments, linked changes,
  reopens. It is separate from `fetch` because it costs extra round trips and
  most callers do not need it.

Adapters normalise at this boundary and nowhere else. By the time a `Ticket`
leaves an adapter it has UTC datetimes, a `state` that is exactly "open" or
"closed", and labels as a tuple of plain strings - whatever contortion the
tracker's own API needed to get there stays inside the adapter.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, ClassVar, Protocol, runtime_checkable

import httpx

from ..schemas import LinkedChange, Ticket, TicketDetail, TicketQuery


class TrackerError(RuntimeError):
    """A tracker could not answer.

    Carries the human-facing reason rather than an HTTP status, because the
    thing that goes wrong in practice is a token without the right scope or a
    project path that does not exist, and a bare 404 says neither.
    """


@runtime_checkable
class Tracker(Protocol):
    name: ClassVar[str]

    def search(self, query: TicketQuery) -> list[Ticket]: ...

    def fetch(self, project: str, key: str) -> Ticket: ...

    def detail(self, ticket: Ticket) -> TicketDetail: ...

    def changed_files(self, project: str, change: LinkedChange) -> tuple[str, ...]:
        """Which files the change touched.

        The fourth method, and the only optional one: an empty tuple is a
        valid answer and Jira always gives it, because there is no supported
        API for the development panel. Callers treat this as a bonus, never as
        a fact they can rely on.

        It earns its place because of what it makes possible. "The last three
        tickets about the destination filter changed these five files" is not
        something a language model can infer from a repository - it only
        exists in the tracker's history, and it is usually the single most
        useful sentence you can hand someone starting a ticket.
        """
        return ()


_REGISTRY: dict[str, type] = {}


def register(cls: type) -> type:
    """Add an adapter to the registry, keyed by its `name`."""
    _REGISTRY[cls.name] = cls
    return cls


def available() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def build(name: str, **config: Any) -> Tracker:
    """Construct the named adapter.

    An unknown name is a configuration mistake worth naming precisely: the
    caller almost always typed a tracker this build does not carry, and the
    list of ones it does carry is the fastest way to see that.
    """
    try:
        cls = _REGISTRY[name]
    except KeyError:
        known = ", ".join(available()) or "none"
        raise TrackerError(f"unknown tracker {name!r}. This build knows: {known}") from None
    return cls(**config)


def utc(value: str | None) -> datetime | None:
    """Parse a tracker timestamp into an aware UTC datetime.

    Every tracker here emits ISO 8601, but they disagree about the timezone
    suffix: GitLab sends `+02:00`, Jira sends `+0200` with no colon, GitHub
    sends `Z`. All three are handled so callers never have to.
    """
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # Jira: +0200 -> +02:00. fromisoformat accepts the colonless form only
    # from 3.11 on some paths, and normalising is cheaper than finding out.
    if len(text) >= 5 and text[-5] in "+-" and text[-3] != ":":
        text = text[:-2] + ":" + text[-2:]
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


_NEXT_LINK = re.compile(r'<([^>]+)>\s*;\s*rel="next"')


def next_link(header: str | None) -> str | None:
    """The `rel="next"` URL from a Link header, if there is one."""
    if not header:
        return None
    match = _NEXT_LINK.search(header)
    return match.group(1) if match else None


def walk(
    client: httpx.Client,
    url: str,
    params: dict[str, Any],
    *,
    max_pages: int = 20,
    page_size: int = 100,
) -> Iterator[list[dict[str, Any]]]:
    """Yield pages from a REST collection, following the Link header.

    **Following `rel="next"` rather than counting pages**, and that distinction
    turned out to matter. GitHub's issues endpoint has moved to cursor paging
    behind the same `page=` parameter: its Link header now carries an `after=`
    cursor, and asking for `page=1` twice with identical parameters returned
    100 rows once and 20 the next time. A corpus built by counting pages was
    therefore silently truncated at whatever the first page happened to give -
    a profile built on 20 tickets when 60 were asked for, with nothing
    anywhere saying so.

    The Link header is what both GitHub and GitLab document, it survives a
    server switching to cursors underneath, and it ends the walk honestly:
    no next link means no more pages.

    `max_pages` is a stop, not a target. A collection with no end is a bug
    somewhere, and an unbounded crawl is a worse way to find out.
    """
    first = True
    pages = 0
    while pages < max_pages:
        if first:
            response = client.get(url, params={**params, "per_page": page_size})
            first = False
        else:
            response = client.get(url)
        if response.status_code >= 400:
            raise TrackerError(f"{response.status_code} from {url}: {response.text[:200]}")
        batch = response.json()
        if not isinstance(batch, list):
            raise TrackerError(f"expected a list from {url}, got {type(batch).__name__}")
        pages += 1
        yield batch

        following = next_link(response.headers.get("link"))
        if not following:
            return
        url = following


def paginate(
    client: httpx.Client,
    url: str,
    params: dict[str, Any],
    *,
    limit: int,
    page_size: int = 100,
) -> list[dict[str, Any]]:
    """Collect rows from a collection until `limit` or the end."""
    out: list[dict[str, Any]] = []
    for batch in walk(client, url, params, page_size=page_size):
        out.extend(batch)
        if len(out) >= limit:
            break
    return out[:limit]
