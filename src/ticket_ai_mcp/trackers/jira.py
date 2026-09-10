# SPDX-License-Identifier: MIT

"""Jira issues - Cloud and self-hosted Server / Data Center.

These are two products wearing one name, and the differences are not cosmetic:

|                | Cloud            | Server / Data Center    |
|----------------|------------------|-------------------------|
| REST version   | v3               | v2                      |
| Description    | ADF, a JSON tree | wiki markup, a string   |
| Search         | `/search/jql`    | `/search`               |
| Paging         | `nextPageToken`  | `startAt` and `total`   |
| Credentials    | email + token    | a PAT, sent as Bearer   |

Which one an instance is gets worked out from `serverInfo` on first use, so
nobody has to know or configure it. `TICKET_AI_JIRA_API` overrides that.

Both description formats are converted to **Markdown**, never to plain text.
Everything downstream counts structure by looking for Markdown, so a converter
that produced readable prose would be silently deleting the evidence - which is
exactly what the ADF path did until a live board showed a code-block rate of 0%
for a project whose tickets are mostly stack traces.

Two limits that apply to both:

- **No public API for linked branches or merge requests.** The development
  panel is served by an internal endpoint that is not supported for third
  parties. Remote links are used instead, which catches teams that link their
  changes and misses teams that rely on smart commits. `detail` reports nothing
  rather than guessing, so the ranking is weaker here than on GitLab or GitHub.
- **Reopens have to be read out of the changelog**, one transition at a time.

`project` is the Jira project key - `PROJ`, not a numeric id.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from ..schemas import Comment, LinkedChange, Ticket, TicketDetail, TicketQuery
from .base import TrackerError, rate_limit_message, register, utc

# Jira has no single "closed" state: a workflow can end in Done, Closed,
# Resolved, Cancelled or anything a project admin invented. The status
# *category* is the stable signal, and it only ever takes three values.
_DONE_CATEGORY = "done"


def adf_to_text(node: Any) -> str:
    """Render an Atlassian Document Format tree as Markdown.

    Markdown rather than plain text, and that is the whole point of this
    function. Everything downstream counts structure - headings, code blocks,
    images, links - by looking for Markdown, so a flattener that produced
    readable prose would be quietly deleting the evidence.

    It did, for a while. Against a live Hibernate board, nine of twenty-five
    descriptions contained a real `codeBlock` node and the profile reported
    that 0% of tickets had a code block, because the fences never made it out
    of here. A heading node would have been erased the same way, which would
    have disabled the template detection - the core of the tool - on every
    Jira project that uses one.

    Not a full renderer. Panels and macros contribute their text and lose
    their chrome, which is fine: nothing counts those.
    """
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_to_text(n) for n in node)
    if not isinstance(node, dict):
        return ""

    kind = node.get("type")
    attrs = node.get("attrs") or {}

    if kind == "text":
        text = node.get("text") or ""
        # A link lives in a mark on the text node, not in a node of its own.
        for mark in node.get("marks") or ():
            if isinstance(mark, dict) and mark.get("type") == "link":
                href = (mark.get("attrs") or {}).get("href")
                if href:
                    return f"[{text}]({href})"
        return text
    if kind == "hardBreak":
        return "\n"
    # Jira's smart links carry the url in attrs and have no text content, so
    # they vanish entirely unless they are handled here.
    if kind in ("inlineCard", "blockCard", "embedCard"):
        return attrs.get("url") or ""
    if kind in ("media", "mediaInline"):
        name = attrs.get("alt") or attrs.get("id") or "attachment"
        return f"![{name}](/uploads/{attrs.get('id', 'attachment')})"

    inner = adf_to_text(node.get("content"))
    if kind == "heading":
        level = min(int(attrs.get("level", 2) or 2), 6)
        return f"{'#' * level} {inner.strip()}\n\n"
    if kind == "codeBlock":
        return f"```\n{inner.strip()}\n```\n\n"
    if kind in ("paragraph", "blockquote", "panel"):
        return inner + "\n\n"
    if kind == "listItem":
        return "- " + inner.strip() + "\n"
    if kind in ("bulletList", "orderedList", "mediaGroup", "mediaSingle"):
        return inner + "\n"
    if kind == "rule":
        return "---\n\n"
    return inner


# --- Jira Server / Data Center wiki markup ---------------------------------
#
# Server and Data Center predate ADF and send the description as a string in
# Atlassian's own wiki syntax. It has to become Markdown for exactly the reason
# ADF does: everything downstream counts structure by looking for Markdown.

# Code first, and its contents are parked before anything else runs. A stack
# trace is full of asterisks and square brackets, and every rule below would
# happily mangle them.
_WIKI_CODE = re.compile(r"\{(code|noformat)(?::[^}]*)?\}(.*?)\{\1\}", re.DOTALL)
_WIKI_HEADING = re.compile(r"^h([1-6])\.\s*(.+?)\s*$", re.MULTILINE)
# `# item` is an ordered list in Jira and a level-one heading in Markdown.
# Left alone, every numbered step in every ticket becomes a heading and the
# template detection is buried in noise.
_WIKI_LIST = re.compile(r"^([*#]{1,6})\s+(?=\S)", re.MULTILINE)
_WIKI_LINK = re.compile(r"\[([^\]|\n]+)\|(https?://[^\]\s]+)\]")
_WIKI_BARE_LINK = re.compile(r"\[(https?://[^\]\s]+)\]")
_WIKI_QUOTE = re.compile(r"\{quote\}(.*?)\{quote\}", re.DOTALL)
_WIKI_PANEL = re.compile(r"\{(?:panel|color|anchor)(?::[^}]*)?\}")
# Bold, but not a list bullet and not part of a word.
_WIKI_BOLD = re.compile(r"(?<![\w*])\*([^*\n]{1,200})\*(?![\w*])")


def wiki_to_markdown(text: str | None) -> str:
    """Convert Jira Server wiki markup to Markdown.

    The ordering is the whole trick, and two steps have to be in the order
    they are in:

    - Code blocks are lifted out first, so no inline rule can touch a stack
      trace full of asterisks and brackets.
    - **Lists are converted before headings.** In wiki markup `#` always
      starts an ordered list and a heading is always `h2.`, so there is no
      ambiguity - but the moment `h2.` has become `## `, the list rule sees a
      `#` at the start of a line and turns the heading into a list item. Doing
      headings first quietly produced `1. Problem` where `## Problem` belonged.
    - Bold comes after lists, because `*` both starts a bullet and wraps bold
      and telling them apart afterwards is guesswork.
    """
    if not text:
        return ""

    blocks: list[str] = []

    def park(match: re.Match[str]) -> str:
        blocks.append(match.group(2).strip("\n"))
        return f"\x00{len(blocks) - 1}\x00"

    def listify(match: re.Match[str]) -> str:
        marker = match.group(1)
        indent = "  " * (len(marker) - 1)
        return f"{indent}1. " if marker[-1] == "#" else f"{indent}- "

    out = _WIKI_CODE.sub(park, text)
    out = _WIKI_LIST.sub(listify, out)
    out = _WIKI_HEADING.sub(lambda m: f"{'#' * int(m.group(1))} {m.group(2)}", out)
    out = _WIKI_LINK.sub(r"[\1](\2)", out)
    out = _WIKI_BARE_LINK.sub(r"\1", out)
    out = _WIKI_QUOTE.sub(
        lambda m: "\n".join(f"> {line}" for line in m.group(1).strip().split("\n")), out
    )
    out = _WIKI_PANEL.sub("", out)
    out = _WIKI_BOLD.sub(r"**\1**", out)

    for i, block in enumerate(blocks):
        out = out.replace(f"\x00{i}\x00", f"```\n{block}\n```")
    return out


@register
class JiraTracker:
    name = "jira"

    # Asking for every field on every issue is slow and mostly wasted; these
    # are the ones this tool actually reads.
    FIELDS = "summary,description,status,labels,reporter,assignee,created,updated,resolutiondate"

    def __init__(
        self,
        *,
        url: str,
        email: str = "",
        token: str = "",
        api: str = "auto",
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not url:
            raise TrackerError("jira needs a base url, for example https://acme.atlassian.net")
        if api not in ("auto", "cloud", "server"):
            raise TrackerError(f"api must be auto, cloud or server, not {api!r}")

        self.url = url.rstrip("/")
        self._api = api
        self._anonymous = not token

        # Three credential shapes, because the two products want different
        # ones and nobody should have to know which they are on:
        #   nothing          - a public board, and many are
        #   email + token    - Cloud's Basic auth, and Server with a password
        #   token by itself  - Server's personal access token, sent as Bearer
        headers = {"Accept": "application/json"}
        auth = None
        if email and token:
            auth = (email, token)
        elif token:
            headers["Authorization"] = f"Bearer {token}"
        elif email:
            raise TrackerError(
                "an account email on its own is not a credential. Add "
                "TICKET_AI_JIRA_TOKEN, or unset both to read a public instance."
            )

        self._client = client or httpx.Client(
            base_url=f"{self.url}/rest/api",
            auth=auth,
            headers=headers,
            timeout=timeout,
        )

    @property
    def _v(self) -> str:
        """The REST version this instance speaks, worked out once and cached."""
        if self._api == "auto":
            self._api = self._detect()
        return "3" if self._api == "cloud" else "2"

    def _detect(self) -> str:
        """Ask the instance which product it is.

        `serverInfo` is served by both under the v2 path and reports a
        `deploymentType` - the only answer that is not a guess. When it cannot
        be reached at all, the hostname decides: Cloud is always on
        atlassian.net, so anything else is far more likely to be self-hosted.
        """
        try:
            response = self._client.get("/2/serverInfo")
            if response.is_redirect:
                response = self._follow(response)
            if response is not None and response.status_code < 400:
                kind = (response.json() or {}).get("deploymentType", "")
                if kind:
                    return "cloud" if kind.lower() == "cloud" else "server"
        except (httpx.HTTPError, ValueError):
            pass
        return "cloud" if ".atlassian.net" in self.url else "server"

    def _follow(self, response: httpx.Response) -> httpx.Response | None:
        """Deal with a Jira that lives somewhere other than where it answers.

        A company keeps a vanity hostname - issues.example.com - and Atlassian
        redirects it permanently to example.atlassian.net. Every browser
        follows that and nobody notices, so it is the URL people paste. The
        client does not follow redirects, so detection saw a 301, failed to
        read JSON out of it, fell back to guessing by hostname, guessed Server
        because the vanity name is not atlassian.net, and then asked a Cloud
        instance for a Server endpoint. The error was "answered with something
        that is not JSON", which sends someone to check their Jira version.

        Where a credential is involved this refuses instead of following. A
        token belongs to the host it was issued for, and forwarding it to
        wherever a redirect points is how a credential ends up somewhere its
        owner did not choose - so the message names the real URL and lets them
        move it deliberately.
        """
        target = response.headers.get("location", "")
        if not target.startswith("https://"):
            return None
        home = target.split("/rest/", 1)[0].rstrip("/")
        if home == self.url:
            return None
        if not self._anonymous:
            raise TrackerError(
                f"{self.url} redirects to {home}. That is where this Jira really "
                "lives - point TICKET_AI_JIRA_URL at it. The credential is not "
                "sent to a host it was not configured for, so this is not done "
                "automatically."
            )
        self.url = home
        self._client.base_url = f"{home}/rest/api"
        return self._client.get("/2/serverInfo")

    def search(self, query: TicketQuery) -> list[Ticket]:
        clauses = [f'project = "{query.project}"']
        if query.state == "open":
            clauses.append(f'statusCategory != "{_DONE_CATEGORY}"')
        elif query.state == "closed":
            clauses.append(f'statusCategory = "{_DONE_CATEGORY}"')
        for label in query.labels:
            clauses.append(f'labels = "{label}"')
        if query.updated_after:
            clauses.append(f'updated >= "{query.updated_after:%Y-%m-%d}"')
        if query.text:
            # Escaping is deliberately conservative: a quote or backslash in a
            # search string is a syntax error in JQL, not a clever query.
            safe = query.text.replace("\\", "\\\\").replace('"', '\\"')
            clauses.append(f'text ~ "{safe}"')
        jql = " AND ".join(clauses) + " ORDER BY updated DESC"

        # The two products page completely differently. Cloud hands back an
        # opaque nextPageToken; Server counts from startAt and tells you the
        # total. There is no shared shape to factor out here, so they stay as
        # two short loops rather than one branchy one.
        out: list[Ticket] = []
        if self._v == "3":
            token: str | None = None
            while len(out) < query.limit:
                params: dict[str, Any] = {
                    "jql": jql,
                    "maxResults": min(100, query.limit - len(out)),
                    "fields": self.FIELDS,
                }
                if token:
                    params["nextPageToken"] = token
                body = self._get("/search/jql", params)
                issues = body.get("issues") or []
                out.extend(self._to_ticket(i, query.project) for i in issues)
                token = body.get("nextPageToken")
                if not token or not issues:
                    break
        else:
            start = 0
            while len(out) < query.limit:
                body = self._get(
                    "/search",
                    {
                        "jql": jql,
                        "startAt": start,
                        "maxResults": min(100, query.limit - len(out)),
                        "fields": self.FIELDS,
                    },
                )
                issues = body.get("issues") or []
                out.extend(self._to_ticket(i, query.project) for i in issues)
                start += len(issues)
                if not issues or start >= int(body.get("total", 0)):
                    break
        return out[: query.limit]

    def fetch(self, project: str, key: str) -> Ticket:
        body = self._get(f"/issue/{key}", {"fields": self.FIELDS})
        return self._to_ticket(body, project)

    def detail(self, ticket: Ticket) -> TicketDetail:
        comments_body = self._get(f"/issue/{ticket.key}/comment", {"maxResults": 100})
        comments = tuple(
            Comment(
                author=(c.get("author") or {}).get("displayName", "?"),
                body=self.body_text(c.get("body")).strip(),
                created_at=utc(c.get("created")),
                system=False,
            )
            for c in (comments_body.get("comments") or [])
            if utc(c.get("created")) is not None
        )

        # Remote links are the only supported way to see an attached change.
        # A team whose MRs never appear here is not a team with no MRs.
        linked: list[LinkedChange] = []
        try:
            for link in self._get_raw(f"/issue/{ticket.key}/remotelink") or []:
                obj = link.get("object") or {}
                linked.append(
                    LinkedChange(
                        ref=obj.get("title") or "",
                        title=obj.get("title") or "",
                        url=obj.get("url") or "",
                        state="linked",
                        merged=False,
                    )
                )
        except TrackerError:
            pass

        changelog = self._get(f"/issue/{ticket.key}", {"expand": "changelog", "fields": "status"})
        reopens = 0
        for entry in (changelog.get("changelog") or {}).get("histories") or []:
            for item in entry.get("items") or []:
                if item.get("field") == "status" and self._is_reopen(item):
                    reopens += 1

        return TicketDetail(
            ticket=ticket,
            comments=comments,
            linked_changes=tuple(linked),
            reopen_count=reopens,
        )

    @staticmethod
    def _is_reopen(item: dict[str, Any]) -> bool:
        """A transition out of a done-looking status and back into work.

        Status *names* are all we get in a changelog item, so this matches on
        the handful of words teams actually end a workflow with. It will miss a
        workflow whose final status is called something else entirely, which is
        why a reopen count of zero is treated as weak evidence downstream, not
        as proof.
        """
        was = (item.get("fromString") or "").strip().lower()
        now = (item.get("toString") or "").strip().lower()
        done_words = {"done", "closed", "resolved", "complete", "completed"}
        return was in done_words and now not in done_words

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        body = self._request(path, params)
        return body if isinstance(body, dict) else {}

    def _get_raw(self, path: str) -> list[dict[str, Any]]:
        body = self._request(path, {})
        return body if isinstance(body, list) else []

    def body_text(self, value: Any) -> str:
        """Turn a description or comment body into Markdown, either dialect.

        Cloud sends a document tree, Server sends a wiki-markup string. The
        type is the discriminator, which is more reliable than the version
        flag: an instance mid-migration can serve either.
        """
        if isinstance(value, str):
            return wiki_to_markdown(value)
        return adf_to_text(value)

    def _request(self, path: str, params: dict[str, Any]) -> Any:
        # Paths are written version-free and get the right prefix here, so no
        # caller has to think about which product it is talking to.
        path = f"/{self._v}{path}"
        try:
            response = self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise TrackerError(f"could not reach {path}: {exc}") from exc
        if response.status_code == 404:
            raise TrackerError(f"jira has no {path}. Check the issue key and the project.")
        if response.status_code in (401, 403):
            if self._anonymous:
                raise TrackerError(
                    f"{self.url} will not serve {path} anonymously. Set "
                    "TICKET_AI_JIRA_EMAIL and TICKET_AI_JIRA_TOKEN - the token "
                    "comes from id.atlassian.com and is not the account password."
                )
            raise TrackerError("jira rejected the credentials. An API token is not a password.")
        if response.status_code >= 400:
            raise TrackerError(rate_limit_message(response, path))
        try:
            return response.json()
        except ValueError:
            # A 200 that is not JSON means this is not the API we think it is.
            # In practice it is always the same cause: a Jira Server or Data
            # Center instance, which serves REST v2 and answers a v3 path with
            # an HTML page. Saying so beats a JSONDecodeError from deep inside
            # the standard library.
            raise TrackerError(
                f"{self.url} answered {path} with something that is not JSON. "
                "This build speaks Jira Cloud's REST v3. A self-hosted Jira "
                "Server or Data Center instance serves REST v2 instead and is "
                "not supported yet."
            ) from None

    def _to_ticket(self, row: dict[str, Any], project: str) -> Ticket:
        fields = row.get("fields") or {}
        status = fields.get("status") or {}
        category = (status.get("statusCategory") or {}).get("key", "")
        assignee = fields.get("assignee") or {}
        key = row.get("key") or ""
        return Ticket(
            uid=f"jira:{project}:{key}",
            key=key,
            title=fields.get("summary") or "",
            description=self.body_text(fields.get("description")).strip(),
            state="closed" if category == _DONE_CATEGORY else "open",
            labels=tuple(fields.get("labels") or ()),
            author=(fields.get("reporter") or {}).get("displayName", "?"),
            assignees=(assignee.get("displayName", "?"),) if assignee else (),
            created_at=utc(fields.get("created")),
            updated_at=utc(fields.get("updated")),
            closed_at=utc(fields.get("resolutiondate")),
            url=f"{self.url}/browse/{key}",
            tracker="jira",
            project=project,
            raw=row,
        )
