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


def rate_limit_message(response: httpx.Response, url: str) -> str:
    """Turn an HTTP failure into the sentence that helps.

    A rate limit is not a fault and it is not a configuration mistake - it is
    a wait. It arrives as a 403 or 429 looking exactly like a permission
    problem, and mining a few hundred tickets reaches one often enough that
    "403 Forbidden" sends people to check a token that was never wrong.

    Both GitHub and GitLab say when the window resets; that number is the
    whole answer, so it is what gets reported.
    """
    status = response.status_code
    headers = response.headers
    remaining = headers.get("x-ratelimit-remaining")
    retry_after = headers.get("retry-after")
    reset = headers.get("x-ratelimit-reset") or headers.get("ratelimit-reset")

    limited = status == 429 or (status == 403 and remaining == "0")
    if not limited and status == 403 and "rate limit" in response.text.lower():
        limited = True

    if limited:
        when = ""
        if retry_after and retry_after.isdigit():
            when = f" Try again in about {round(int(retry_after) / 60)} minutes."
        elif reset and reset.isdigit():
            from datetime import datetime

            seconds = int(reset) - int(datetime.now(UTC).timestamp())
            if seconds > 0:
                when = f" The window resets in about {max(1, round(seconds / 60))} minutes."
        return (
            f"The tracker is rate limiting: {status} on {url}."
            f"{when} Nothing is wrong with the token."
            " Mining reads the history of every ticket, so a large --sample can"
            " reach a limit; a smaller one, or a wait, is the fix."
        )
    return f"{status} from {url}: {response.text[:200]}"


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
            raise TrackerError(rate_limit_message(response, url))

        batch = response.json()
        if not isinstance(batch, list):
            # A collection that answers with an object is telling you
            # something, and "expected a list" throws it away. The case that
            # found this: a repository had been transferred, so GitHub
            # answered 301 with a body naming the new location - which is a
            # fixable configuration problem, not a parsing failure.
            detail = ""
            if isinstance(batch, dict):
                message = batch.get("message") or ""
                moved = batch.get("url") or ""
                if response.status_code in (301, 302, 307, 308) or "moved" in message.lower():
                    raise TrackerError(
                        f"{url} has moved. GitHub says: {message or 'Moved Permanently'}. "
                        "The project was probably renamed or transferred - point "
                        "TICKET_AI_PROJECT at its current owner/repo."
                        + (f" It now lives at {moved}." if moved else "")
                    )
                detail = f": {message}" if message else f": {list(batch)[:5]}"
            raise TrackerError(
                f"expected a list of items from {url}, got {type(batch).__name__}{detail}"
            )
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
