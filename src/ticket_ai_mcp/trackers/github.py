# SPDX-License-Identifier: MIT

"""GitHub issues, via the REST API.

Included as much to keep the abstraction honest as to be used: a third adapter
is what proves the first two were not just one tracker with a coat of paint.

The trap here is famous and still catches people: **the issues endpoint returns
pull requests too.** GitHub models a PR as an issue with extra fields, so a
repository with 40 issues and 900 PRs looks like 940 issues until you filter on
the `pull_request` key. That filter is in `_rows`.

`project` is `owner/repo`.
"""

from __future__ import annotations

from typing import Any

import httpx

from ..schemas import Comment, LinkedChange, Ticket, TicketDetail, TicketQuery
from .base import TrackerError, rate_limit_message, register, utc, walk

PAGE_SIZE = 100
# A ceiling on the walk, so a repository that is all pull requests costs ten
# requests and an honest short answer rather than an unbounded crawl.
MAX_PAGES = 10


@register
class GitHubTracker:
    name = "github"

    def __init__(
        self,
        *,
        token: str,
        url: str = "https://api.github.com",
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not token:
            raise TrackerError(
                "github needs a token with repo read access. Set TICKET_AI_GITHUB_TOKEN."
            )
        self.url = url.rstrip("/")
        self._client = client or httpx.Client(
            base_url=self.url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=timeout,
        )

    def search(self, query: TicketQuery) -> list[Ticket]:
        # Text search needs a different endpoint with a different response
        # shape, so the two paths stay separate rather than sharing a branchy
        # one.
        if query.text:
            return self._text_search(query)

        params: dict[str, Any] = {"state": query.state or "all", "sort": "updated"}
        if query.labels:
            params["labels"] = ",".join(query.labels)
        if query.updated_after:
            params["since"] = query.updated_after.isoformat()

        # Walk until there are enough *issues*, not enough rows, and follow the
        # Link header to get from page to page. Both halves were learned the
        # hard way: over-fetching by a fixed multiple returned ten issues when
        # sixty were wanted on a repository where pull requests outnumber
        # issues fifty to one, and counting pages returned twenty because this
        # endpoint now pages by cursor underneath. See `base.walk`.
        found: list[dict[str, Any]] = []
        rows_seen = 0
        for batch in walk(
            self._client,
            f"/repos/{query.project}/issues",
            params,
            max_pages=MAX_PAGES,
            page_size=PAGE_SIZE,
        ):
            rows_seen += len(batch)
            found.extend(self._rows(batch))
            if len(found) >= query.limit:
                break

        # Hundreds of rows and not one issue means this repository has its
        # issue tracker turned off and the endpoint is serving pull requests
        # only. Returning an empty list would send the caller off to build a
        # profile from nothing and wonder why every rate is zero.
        if rows_seen and not found:
            raise TrackerError(self._why_no_issues(query.project, rows_seen))
        return [self._to_ticket(r, query.project) for r in found][: query.limit]

    def _why_no_issues(self, project: str, rows_seen: int) -> str:
        """Turn the inference into a fact, with one request.

        The repository endpoint reports `has_issues`, so the guess this used to
        make - "most likely has issues disabled" - is answerable outright, and
        only ever needs asking on the rare board that reaches this line.
        Checked against two real repositories that hit it: both are
        `has_issues: false`, so the inference had been right, and saying it
        without the hedge is worth one call on a path that has already spent
        ten pages.
        """
        try:
            response = self._client.get(f"/repos/{project}")
            # Anything but the object GitHub documents falls through to the
            # inference. This function exists to produce a good message, so it
            # is not allowed to raise while doing it.
            body = response.json() if response.status_code < 400 else None
            if isinstance(body, dict) and body.get("has_issues") is False:
                return (
                    f"{project} has issues disabled - GitHub says so, and all "
                    f"{rows_seen} rows on its issues endpoint are pull requests. "
                    "Whatever this project uses for tickets, it is not here."
                )
        except (httpx.HTTPError, ValueError):
            pass
        return (
            f"{project} returned {rows_seen} rows and no issues - every one was a "
            "pull request. The repository most likely has issues disabled and uses "
            "another tracker. Check the Issues tab."
        )

    def _text_search(self, query: TicketQuery) -> list[Ticket]:
        bits = [f"repo:{query.project}", "is:issue", query.text or ""]
        if query.state:
            bits.append(f"state:{query.state}")
        bits.extend(f'label:"{label}"' for label in query.labels)
        response = self._client.get(
            "/search/issues",
            params={"q": " ".join(b for b in bits if b), "per_page": min(100, query.limit)},
        )
        if response.status_code >= 400:
            raise TrackerError(rate_limit_message(response, "/search/issues"))
        items = (response.json() or {}).get("items") or []
        return [self._to_ticket(r, query.project) for r in items][: query.limit]

    def fetch(self, project: str, key: str) -> Ticket:
        number = key.lstrip("#")
        response = self._client.get(f"/repos/{project}/issues/{number}")
        if response.status_code == 404:
            raise TrackerError(f"no issue {key} in {project}, or the token cannot see it.")
        if response.status_code >= 400:
            raise TrackerError(rate_limit_message(response, f"issue {key}"))
        return self._to_ticket(response.json(), project)

    def detail(self, ticket: Ticket) -> TicketDetail:
        number = ticket.key.lstrip("#")
        base = f"/repos/{ticket.project}/issues/{number}"

        comments = tuple(
            Comment(
                author=(c.get("user") or {}).get("login", "?"),
                body=c.get("body") or "",
                created_at=utc(c.get("created_at")),
                system=False,
            )
            for c in self._list(f"{base}/comments")
            if utc(c.get("created_at")) is not None
        )

        # The timeline is the only place a cross-referencing PR and a reopen
        # both show up.
        linked: list[LinkedChange] = []
        reopens = 0
        for event in self._list(f"{base}/timeline"):
            name = event.get("event")
            if name == "reopened":
                reopens += 1
                continue
            if name != "cross-referenced":
                continue
            source = (event.get("source") or {}).get("issue") or {}
            if "pull_request" not in source:
                continue
            pr = source.get("pull_request") or {}
            linked.append(
                LinkedChange(
                    ref=f"#{source.get('number')}",
                    title=source.get("title") or "",
                    url=source.get("html_url") or "",
                    state=source.get("state") or "",
                    merged=bool(pr.get("merged_at")),
                )
            )

        return TicketDetail(
            ticket=ticket,
            comments=comments,
            linked_changes=tuple(linked),
            reopen_count=reopens,
        )

    def changed_files(self, project: str, change: LinkedChange) -> tuple[str, ...]:
        """The paths a pull request touched.

        Capped at one page. A pull request with more than a hundred changed
        files is a refactor, and listing all of them would bury the answer
        rather than give it.
        """
        number = change.ref.lstrip("#")
        if not number.isdigit():
            return ()
        rows = self._list(f"/repos/{project}/pulls/{number}/files")
        return tuple(r["filename"] for r in rows if r.get("filename"))

    def _list(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        try:
            response = self._client.get(path, params={**(params or {}), "per_page": PAGE_SIZE})
        except httpx.HTTPError as exc:
            raise TrackerError(f"could not reach {path}: {exc}") from exc
        if response.status_code in (403, 404, 410):
            return []
        if response.status_code >= 400:
            raise TrackerError(rate_limit_message(response, path))
        body = response.json()
        return body if isinstance(body, list) else []

    @staticmethod
    def _rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Drop the pull requests GitHub mixes into the issues list."""
        return [r for r in rows if "pull_request" not in r]

    def _to_ticket(self, row: dict[str, Any], project: str) -> Ticket:
        number = row.get("number")
        return Ticket(
            uid=f"github:{project}#{number}",
            key=f"#{number}",
            title=row.get("title") or "",
            description=row.get("body") or "",
            state=row.get("state") or "",
            labels=tuple(
                label_name
                for label in (row.get("labels") or ())
                if (label_name := label.get("name") if isinstance(label, dict) else label)
            ),
            author=(row.get("user") or {}).get("login", "?"),
            assignees=tuple(a.get("login", "?") for a in (row.get("assignees") or ())),
            created_at=utc(row.get("created_at")),
            updated_at=utc(row.get("updated_at")),
            closed_at=utc(row.get("closed_at")),
            url=row.get("html_url") or "",
            tracker="github",
            project=project,
            raw=row,
        )
