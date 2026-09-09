# SPDX-License-Identifier: MIT

"""GitLab issues, via REST v4.

Self-managed instances are the common case here, so the base URL is a required
setting rather than a default pointing at gitlab.com.

Two GitLab quirks are worth knowing, because both cost an afternoon to
rediscover:

- An issue has two numbers. `id` is unique across the whole instance and is
  useless in a URL; `iid` is the number a human sees and the one every issue
  endpoint takes. This adapter uses `iid` throughout.
- `state` is "opened", not "open". It is normalised on the way out, so the
  string never leaks past this file.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from ..schemas import Comment, LinkedChange, Ticket, TicketDetail, TicketQuery
from .base import TrackerError, paginate, register, utc


@register
class GitLabTracker:
    name = "gitlab"

    def __init__(
        self,
        *,
        url: str,
        token: str,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not url:
            raise TrackerError("gitlab needs a base url, for example https://gitlab.example.com")
        if not token:
            raise TrackerError(
                "gitlab needs a personal access token with the read_api scope. "
                "Set TICKET_AI_GITLAB_TOKEN."
            )
        self.url = url.rstrip("/")
        self._client = client or httpx.Client(
            base_url=f"{self.url}/api/v4",
            headers={"PRIVATE-TOKEN": token},
            timeout=timeout,
        )

    # A project is addressed either by its numeric id or by its full path, and
    # the path has to be percent-encoded slashes and all. Callers pass whichever
    # they have and this sorts it out.
    @staticmethod
    def _project_ref(project: str) -> str:
        return project if project.isdigit() else quote(project, safe="")

    def search(self, query: TicketQuery) -> list[Ticket]:
        params: dict[str, Any] = {"order_by": "updated_at", "sort": "desc"}
        if query.state:
            params["state"] = "opened" if query.state == "open" else query.state
        if query.labels:
            params["labels"] = ",".join(query.labels)
        if query.updated_after:
            params["updated_after"] = query.updated_after.isoformat()
        if query.text:
            params["search"] = query.text
        rows = paginate(
            self._client,
            f"/projects/{self._project_ref(query.project)}/issues",
            params,
            limit=query.limit,
        )
        return [self._to_ticket(row, query.project) for row in rows]

    def fetch(self, project: str, key: str) -> Ticket:
        iid = key.lstrip("#")
        response = self._client.get(f"/projects/{self._project_ref(project)}/issues/{iid}")
        if response.status_code == 404:
            raise TrackerError(
                f"no issue {key} in {project}. Check the project path and that the "
                "token can see the project."
            )
        if response.status_code >= 400:
            raise TrackerError(f"{response.status_code} fetching {key}: {response.text[:200]}")
        return self._to_ticket(response.json(), project)

    def detail(self, ticket: Ticket) -> TicketDetail:
        ref = self._project_ref(ticket.project)
        iid = ticket.key.lstrip("#")
        base = f"/projects/{ref}/issues/{iid}"

        notes = self._get_list(f"{base}/notes", {"sort": "asc", "order_by": "created_at"})
        comments = tuple(
            Comment(
                author=(n.get("author") or {}).get("username", "?"),
                body=n.get("body") or "",
                created_at=utc(n.get("created_at")),
                system=bool(n.get("system")),
            )
            for n in notes
            if utc(n.get("created_at")) is not None
        )

        # related_merge_requests covers both "closes #12" and a plain mention,
        # which is what we want: a linked MR is evidence either way.
        merges = self._get_list(f"{base}/related_merge_requests", {})
        linked = tuple(
            LinkedChange(
                ref=f"!{m.get('iid')}",
                title=m.get("title") or "",
                url=m.get("web_url") or "",
                state=m.get("state") or "",
                merged=(m.get("state") == "merged"),
            )
            for m in merges
        )

        events = self._get_list(f"{base}/resource_state_events", {})
        reopens = sum(1 for e in events if e.get("state") == "reopened")

        return TicketDetail(
            ticket=ticket,
            comments=comments,
            linked_changes=linked,
            reopen_count=reopens,
        )

    def changed_files(self, project: str, change: LinkedChange) -> tuple[str, ...]:
        """The paths a merge request touched.

        `/changes` returns the whole diff, which is far more than is wanted -
        but it is the endpoint every GitLab version has, and only the paths
        are kept. A renamed file reports both names, since either is a
        reasonable place for a reader to start looking.
        """
        iid = change.ref.lstrip("!")
        if not iid.isdigit():
            return ()
        body = self._get(f"/projects/{self._project_ref(project)}/merge_requests/{iid}/changes")
        paths: list[str] = []
        for entry in body.get("changes") or ():
            for key in ("new_path", "old_path"):
                path = entry.get(key)
                if path and path not in paths:
                    paths.append(path)
        return tuple(paths)

    def _get(self, path: str) -> dict[str, Any]:
        try:
            response = self._client.get(path)
        except httpx.HTTPError:
            return {}
        if response.status_code >= 400:
            return {}
        body = response.json()
        return body if isinstance(body, dict) else {}

    def _get_list(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Fetch a sub-resource, treating an absent one as empty.

        Not every endpoint exists on every GitLab version or plan, and a
        missing `resource_state_events` should degrade the ranking, not abort
        the run.
        """
        try:
            response = self._client.get(path, params={**params, "per_page": 100})
        except httpx.HTTPError as exc:
            raise TrackerError(f"could not reach {path}: {exc}") from exc
        if response.status_code in (403, 404):
            return []
        if response.status_code >= 400:
            raise TrackerError(f"{response.status_code} from {path}: {response.text[:200]}")
        body = response.json()
        return body if isinstance(body, list) else []

    def _to_ticket(self, row: dict[str, Any], project: str) -> Ticket:
        iid = row.get("iid")
        state = row.get("state") or ""
        return Ticket(
            uid=f"gitlab:{project}#{iid}",
            key=f"#{iid}",
            title=row.get("title") or "",
            description=row.get("description") or "",
            state="open" if state == "opened" else state,
            labels=tuple(row.get("labels") or ()),
            author=(row.get("author") or {}).get("username", "?"),
            assignees=tuple(a.get("username", "?") for a in (row.get("assignees") or ())),
            created_at=utc(row.get("created_at")),
            updated_at=utc(row.get("updated_at")),
            closed_at=utc(row.get("closed_at")),
            url=row.get("web_url") or "",
            tracker="gitlab",
            project=project,
            raw=row,
        )
