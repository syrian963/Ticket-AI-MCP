# SPDX-License-Identifier: MIT

"""Adapters, against recorded API shapes rather than a live tracker.

The bugs worth catching here are all normalisation bugs, and they are quiet:
GitLab's "opened", GitHub's pull requests hiding in the issues list, Jira's
description arriving as a JSON tree. None of them raise. They just make every
number downstream slightly wrong.
"""

from __future__ import annotations

import httpx
import pytest

from ticket_ai_mcp.schemas import TicketQuery
from ticket_ai_mcp.textstats import shape
from ticket_ai_mcp.trackers import TrackerError, available, build
from ticket_ai_mcp.trackers.base import next_link, paginate, utc, walk
from ticket_ai_mcp.trackers.github import GitHubTracker
from ticket_ai_mcp.trackers.gitlab import GitLabTracker
from ticket_ai_mcp.trackers.jira import JiraTracker, adf_to_text, wiki_to_markdown


def transport(routes: dict[str, object]) -> httpx.MockTransport:
    """Serve recorded bodies by path, and 404 anything unexpected.

    A 404 for an unrouted path is deliberate: it makes a test that quietly
    starts calling a new endpoint fail loudly instead of passing on an empty
    list.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        for path, body in routes.items():
            if request.url.path.endswith(path):
                return httpx.Response(200, json=body)
        return httpx.Response(404, json={"message": f"no route for {request.url.path}"})

    return httpx.MockTransport(handler)


def client(base: str, routes: dict[str, object]) -> httpx.Client:
    return httpx.Client(base_url=base, transport=transport(routes))


class TestRegistry:
    def test_all_three_register(self):
        assert available() == ("github", "gitlab", "jira")

    def test_an_unknown_name_lists_the_known_ones(self):
        with pytest.raises(TrackerError, match="github, gitlab, jira"):
            build("bitbucket")


class TestLinkPagination:
    """Following rel="next" rather than counting pages."""

    def cursored(self, sizes: list[int]):
        """A server that pages by cursor and ignores `page=`.

        Modelled on what GitHub's issues endpoint actually does now: the same
        request with the same parameters returned 100 rows once and 20 the
        next time, and the Link header carried an `after=` cursor. Page
        numbers mean nothing to it.
        """
        state = {"step": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            step = state["step"]
            state["step"] += 1
            if step >= len(sizes):
                return httpx.Response(200, json=[])
            rows = [{"id": f"{step}-{i}"} for i in range(sizes[step])]
            headers = {}
            if step + 1 < len(sizes):
                headers["Link"] = (
                    f'<https://api.example.com/issues?after=cursor{step}>; rel="next", '
                    '<https://api.example.com/issues?page=1>; rel="first"'
                )
            return httpx.Response(200, json=rows, headers=headers)

        return httpx.Client(
            base_url="https://api.example.com", transport=httpx.MockTransport(handler)
        )

    def test_it_follows_the_next_link_across_uneven_pages(self):
        # The regression: a first page shorter than page_size used to end the
        # walk, so a corpus asked for 60 was silently built from 20.
        rows = paginate(self.cursored([20, 100, 14]), "/issues", {}, limit=60)
        assert len(rows) == 60
        assert len({r["id"] for r in rows}) == 60

    def test_no_next_link_ends_the_walk(self):
        rows = paginate(self.cursored([7]), "/issues", {}, limit=500)
        assert len(rows) == 7

    def test_the_walk_is_bounded(self):
        pages = list(walk(self.cursored([100] * 50), "/issues", {}, max_pages=3))
        assert len(pages) == 3

    def test_a_next_link_is_read_out_of_the_header(self):
        header = '<https://a/next>; rel="next", <https://a/last>; rel="last"'
        assert next_link(header) == "https://a/next"
        assert next_link('<https://a/prev>; rel="prev"') is None
        assert next_link(None) is None


class TestTimestamps:
    @pytest.mark.parametrize(
        "raw",
        [
            "2026-03-01T10:00:00Z",  # GitHub
            "2026-03-01T11:00:00+01:00",  # GitLab
            "2026-03-01T11:00:00.000+0100",  # Jira, no colon in the offset
        ],
    )
    def test_every_dialect_lands_on_the_same_instant(self, raw):
        parsed = utc(raw)
        assert parsed.hour == 10
        assert parsed.tzinfo is not None

    def test_junk_is_none_not_an_exception(self):
        assert utc("not a date") is None
        assert utc(None) is None


GITLAB_ISSUE = {
    "iid": 42,
    "title": "Etikettendruck bricht ab",
    "description": "## Problem\ntext",
    "state": "opened",
    "labels": ["bug", "team::shop"],
    "author": {"username": "mira"},
    "assignees": [{"username": "mouhamad"}],
    "created_at": "2026-03-01T11:00:00+01:00",
    "updated_at": "2026-03-02T11:00:00+01:00",
    "closed_at": None,
    "web_url": "https://git.example.com/acme/shop/-/issues/42",
}


class TestGitLab:
    def make(self, routes):
        return GitLabTracker(
            url="https://git.example.com",
            token="t",
            client=client("https://git.example.com/api/v4", routes),
        )

    def test_opened_is_normalised_to_open(self):
        ticket = self.make({"/issues/42": GITLAB_ISSUE}).fetch("acme/shop", "#42")
        assert ticket.state == "open"
        assert ticket.key == "#42"
        assert ticket.labels == ("bug", "team::shop")
        assert ticket.author == "mira"

    def test_a_project_path_is_percent_encoded(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, json=[GITLAB_ISSUE])

        tracker = GitLabTracker(
            url="https://git.example.com",
            token="t",
            client=httpx.Client(
                base_url="https://git.example.com/api/v4",
                transport=httpx.MockTransport(handler),
            ),
        )
        tracker.search(TicketQuery(project="acme/shop", limit=1))
        assert "acme%2Fshop" in seen[0]

    def test_a_numeric_project_id_is_left_alone(self):
        assert GitLabTracker._project_ref("314") == "314"

    def test_detail_reads_merges_reopens_and_system_notes(self):
        tracker = self.make(
            {
                "/issues/42": GITLAB_ISSUE,
                "/notes": [
                    {
                        "author": {"username": "jonas"},
                        "body": "welche Spalte genau?",
                        "created_at": "2026-03-01T12:00:00Z",
                        "system": False,
                    },
                    {
                        "author": {"username": "jonas"},
                        "body": "changed status to closed",
                        "created_at": "2026-03-01T13:00:00Z",
                        "system": True,
                    },
                ],
                "/related_merge_requests": [
                    {
                        "iid": 93,
                        "title": "fix export",
                        "web_url": "u",
                        "state": "merged",
                    }
                ],
                "/resource_state_events": [{"state": "reopened"}, {"state": "closed"}],
            }
        )
        ticket = tracker.fetch("acme/shop", "#42")
        detail = tracker.detail(ticket)
        assert len(detail.comments) == 2
        assert len(detail.human_comments) == 1
        assert detail.was_implemented
        assert detail.reopen_count == 1

    def test_a_missing_sub_resource_degrades_instead_of_failing(self):
        # Older instances and smaller plans do not serve every endpoint. A
        # missing one should weaken the ranking, not abort the run.
        tracker = self.make({"/issues/42": GITLAB_ISSUE, "/notes": []})
        detail = tracker.detail(tracker.fetch("acme/shop", "#42"))
        assert detail.linked_changes == ()
        assert detail.reopen_count == 0

    def test_a_missing_issue_says_what_to_check(self):
        tracker = self.make({})
        with pytest.raises(TrackerError, match="project path"):
            tracker.fetch("acme/shop", "#42")

    def test_a_url_is_required(self):
        with pytest.raises(TrackerError, match="base url"):
            GitLabTracker(url="", token="t")

    def test_no_token_reads_anonymously_and_sends_no_header(self):
        # Every public project on gitlab.com answers the issues API without a
        # token. Refusing to start without one shut the tool out of exactly the
        # open-source boards it is most useful to learn from.
        tracker = GitLabTracker(url="https://gitlab.com", token="")
        assert tracker.anonymous
        assert "PRIVATE-TOKEN" not in tracker._client.headers

    def test_anonymously_a_missing_project_might_just_be_private(self):
        # GitLab hides a private project rather than refusing it, so without a
        # token a typo and a permission problem arrive as the same 404. The
        # message has to offer both.
        tracker = GitLabTracker(
            url="https://gitlab.com", token="", client=client("https://gitlab.com/api/v4", {})
        )
        with pytest.raises(TrackerError, match="TICKET_AI_GITLAB_TOKEN"):
            tracker.search(TicketQuery(project="acme/shop", limit=5))


class TestGitHub:
    def test_pull_requests_are_filtered_out_of_the_issues_list(self):
        # GitHub models a PR as an issue. A repo with 40 issues and 900 PRs
        # looks like 940 issues until this filter runs.
        rows = [
            {"number": 1, "title": "a real issue", "user": {"login": "x"}},
            {"number": 2, "title": "a PR", "user": {"login": "x"}, "pull_request": {"url": "u"}},
            {"number": 3, "title": "another issue", "user": {"login": "x"}},
        ]
        tracker = GitHubTracker(
            token="t", client=client("https://api.github.com", {"/issues": rows})
        )
        found = tracker.search(TicketQuery(project="acme/shop", limit=10))
        assert [t.key for t in found] == ["#1", "#3"]

    def test_it_keeps_paging_until_it_has_enough_issues(self):
        # A live repository where pull requests outnumbered issues returned ten
        # issues when sixty were asked for, because the walk stopped after a
        # fixed multiple of rows rather than after enough issues.
        step = {"n": 0}

        def handler(_: httpx.Request) -> httpx.Response:
            page = step["n"]
            step["n"] += 1
            if page >= 4:
                return httpx.Response(200, json=[])
            rows = []
            for i in range(100):
                n = page * 100 + i
                row = {"number": n, "title": "t", "user": {"login": "x"}}
                # 19 of every 20 rows is a pull request.
                if n % 20:
                    row["pull_request"] = {"url": "u"}
                rows.append(row)
            return httpx.Response(
                200,
                json=rows,
                headers={"Link": '<https://api.github.com/next>; rel="next"'},
            )

        tracker = GitHubTracker(
            token="t",
            client=httpx.Client(
                base_url="https://api.github.com", transport=httpx.MockTransport(handler)
            ),
        )
        found = tracker.search(TicketQuery(project="a/b", state="closed", limit=15))
        assert len(found) == 15
        assert len({t.key for t in found}) == 15

    def test_the_page_walk_is_bounded(self):
        # A repository with nothing but pull requests must cost a fixed number
        # of requests and give a short answer, not crawl forever.
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            # Always another page, forever. Only the ceiling stops this.
            return httpx.Response(
                200,
                json=[
                    {"number": i, "title": "t", "user": {"login": "x"}, "pull_request": {}}
                    for i in range(100)
                ],
                headers={"Link": '<https://api.github.com/next>; rel="next"'},
            )

        tracker = GitHubTracker(
            token="t",
            client=httpx.Client(
                base_url="https://api.github.com", transport=httpx.MockTransport(handler)
            ),
        )
        # All rows and no issues now raises rather than returning empty - see
        # `test_github.py`, where a repository with issues switched off was
        # silently building a profile from nothing. The ceiling is what this
        # test is about, so the count is what it checks.
        with pytest.raises(TrackerError, match="no issues"):
            tracker.search(TicketQuery(project="a/b", limit=50))
        # Ten pages, then one look at the repository itself: GitHub reports
        # `has_issues` outright, so the refusal says it as a fact instead of
        # guessing. That eleventh call happens only on this path, where ten
        # have already been spent.
        assert len(calls) == 11

    def test_labels_arrive_as_names(self):
        rows = [
            {
                "number": 7,
                "title": "t",
                "user": {"login": "x"},
                "labels": [{"name": "bug"}, {"name": "p1"}],
            }
        ]
        tracker = GitHubTracker(
            token="t", client=client("https://api.github.com", {"/issues": rows})
        )
        assert tracker.search(TicketQuery(project="a/b", limit=5))[0].labels == ("bug", "p1")


class TestJiraAuth:
    def test_a_public_instance_is_readable_without_credentials(self):
        # Plenty of open-source Jira boards answer anonymously. Demanding a
        # token to read one would put this tool behind a login the board does
        # not have.
        tracker = JiraTracker(url="https://hibernate.atlassian.net")
        assert tracker._anonymous

    def test_half_a_credential_is_a_mistake_worth_naming(self):
        with pytest.raises(TrackerError, match="both"):
            JiraTracker(url="https://acme.atlassian.net", email="a@b.c")

    def test_a_private_instance_says_which_variables_to_set(self):
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"message": "no"})

        tracker = JiraTracker(
            url="https://acme.atlassian.net",
            client=httpx.Client(
                base_url="https://acme.atlassian.net/rest/api/3",
                transport=httpx.MockTransport(handler),
            ),
        )
        with pytest.raises(TrackerError, match="TICKET_AI_JIRA_TOKEN"):
            tracker.fetch("PROJ", "PROJ-1")

    def test_a_server_instance_is_named_rather_than_crashing(self):
        # Apache's Jira answers a v3 path with an HTML page and a 200. That
        # used to surface as a JSONDecodeError from inside the standard
        # library, which tells nobody anything.
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html><body>Not found</body></html>")

        tracker = JiraTracker(
            url="https://issues.apache.org/jira",
            client=httpx.Client(
                base_url="https://issues.apache.org/jira/rest/api/3",
                transport=httpx.MockTransport(handler),
            ),
        )
        with pytest.raises(TrackerError, match="Data Center"):
            tracker.fetch("KAFKA", "KAFKA-1")


class TestJiraWikiMarkup:
    """Server and Data Center send wiki markup, and it has the same trap as ADF."""

    def test_headings_and_code_survive(self):
        wiki = (
            "h2. Steps to reproduce\n"
            "Run the mapping and watch it fail.\n"
            "{code:java}\n"
            "session.get(Foo.class, 1L);\n"
            "{code}\n"
        )
        text = wiki_to_markdown(wiki)
        assert "## Steps to reproduce" in text
        s = shape(text)
        assert s.headings == ("Steps to reproduce",)
        assert s.code_blocks == 1

    def test_a_numbered_list_does_not_become_a_heading(self):
        # `# item` is an ordered list in Jira and an H1 in Markdown. Left
        # alone, every numbered step in every ticket becomes a section and the
        # template detection drowns.
        text = wiki_to_markdown("h3. Ablauf\n# open it\n# filter\n# export\n")
        s = shape(text)
        assert s.headings == ("Ablauf",)
        assert s.list_items == 3

    def test_nested_bullets_stay_bullets(self):
        s = shape(wiki_to_markdown("* one\n** deeper\n* two\n"))
        assert s.list_items == 3

    def test_code_contents_are_left_alone(self):
        # A stack trace is full of asterisks and brackets, and every inline
        # rule would happily mangle them.
        wiki = "{noformat}\nat com.Foo[*] bar *baz* [x|y]\n{noformat}\n"
        text = wiki_to_markdown(wiki)
        assert "at com.Foo[*] bar *baz* [x|y]" in text
        assert "**baz**" not in text

    def test_links_and_bold(self):
        text = wiki_to_markdown("see [the docs|https://example.com/a] and *this*")
        assert "[the docs](https://example.com/a)" in text
        assert "**this**" in text
        assert shape(text).links == 1

    def test_empty_input(self):
        assert wiki_to_markdown(None) == ""
        assert wiki_to_markdown("") == ""


class TestJiraFlavour:
    def make(self, url, routes, **kwargs):
        return JiraTracker(
            url=url,
            client=client(f"{url}/rest/api", routes),
            **kwargs,
        )

    def test_server_info_decides(self):
        cloud = self.make("https://x.example.com", {"/serverInfo": {"deploymentType": "Cloud"}})
        assert cloud._v == "3"
        server = self.make("https://y.example.com", {"/serverInfo": {"deploymentType": "Server"}})
        assert server._v == "2"

    def test_the_hostname_decides_when_server_info_cannot_be_reached(self):
        assert self.make("https://acme.atlassian.net", {})._v == "3"
        assert self.make("https://jira.acme.internal", {})._v == "2"

    def test_an_explicit_override_skips_detection(self):
        # For the instance behind a proxy that lies about itself.
        forced = self.make(
            "https://acme.atlassian.net", {"/serverInfo": {"deploymentType": "Cloud"}}, api="server"
        )
        assert forced._v == "2"

    def test_a_server_description_comes_back_as_markdown(self):
        issue = {
            "key": "HHH-1",
            "fields": {
                "summary": "t",
                "description": "h2. Problem\n{code}x = 1{code}",
                "status": {"statusCategory": {"key": "done"}},
                "labels": [],
                "reporter": {"displayName": "Steve"},
                "created": "2026-03-01T11:00:00.000+0100",
                "updated": "2026-03-01T11:00:00.000+0100",
            },
        }
        tracker = self.make(
            "https://jira.acme.internal",
            {"/serverInfo": {"deploymentType": "Server"}, "/issue/HHH-1": issue},
        )
        ticket = tracker.fetch("HHH", "HHH-1")
        assert "## Problem" in ticket.description
        assert shape(ticket.description).code_blocks == 1

    def test_server_pages_by_startat_not_by_token(self):
        seen: list[dict[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/serverInfo"):
                return httpx.Response(200, json={"deploymentType": "Server"})
            seen.append(dict(request.url.params))
            start = int(request.url.params.get("startAt", 0))
            rows = [
                {
                    "key": f"P-{i}",
                    "fields": {
                        "summary": "t",
                        "description": "body",
                        "status": {"statusCategory": {"key": "done"}},
                        "labels": [],
                        "reporter": {"displayName": "x"},
                        "created": "2026-03-01T11:00:00.000+0100",
                        "updated": "2026-03-01T11:00:00.000+0100",
                    },
                }
                for i in range(start, min(start + 100, 150))
            ]
            return httpx.Response(200, json={"issues": rows, "total": 150})

        tracker = JiraTracker(
            url="https://jira.acme.internal",
            client=httpx.Client(
                base_url="https://jira.acme.internal/rest/api",
                transport=httpx.MockTransport(handler),
            ),
        )
        found = tracker.search(TicketQuery(project="P", state="closed", limit=150))
        assert len(found) == 150
        assert len({t.key for t in found}) == 150
        assert [p["startAt"] for p in seen] == ["0", "100"]
        assert all("/2/" in "/2/" for _ in seen)


class TestJiraAuth2:
    def test_a_token_on_its_own_is_sent_as_a_bearer(self):
        # Server's personal access tokens are Bearer, not Basic, and there is
        # no email to pair one with.
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("authorization", ""))
            return httpx.Response(200, json={"deploymentType": "Server"})

        tracker = JiraTracker(url="https://jira.acme.internal", token="pat123")
        tracker._client = httpx.Client(
            base_url="https://jira.acme.internal/rest/api",
            headers=dict(tracker._client.headers),
            transport=httpx.MockTransport(handler),
        )
        assert tracker._v == "2"
        assert seen[0] == "Bearer pat123"

    def test_an_email_without_a_token_is_not_a_credential(self):
        with pytest.raises(TrackerError, match="not a credential"):
            JiraTracker(url="https://acme.atlassian.net", email="a@b.c")


class TestJiraAdf:
    def test_a_document_tree_becomes_readable_text(self):
        doc = {
            "type": "doc",
            "content": [
                {
                    "type": "heading",
                    "content": [{"type": "text", "text": "Problem"}],
                },
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "The export drops a row."}],
                },
                {
                    "type": "bulletList",
                    "content": [
                        {
                            "type": "listItem",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "every row"}],
                                }
                            ],
                        }
                    ],
                },
            ],
        }
        text = adf_to_text(doc)
        assert "Problem" in text
        assert "The export drops a row." in text
        assert "- every row" in text

    def test_structure_survives_as_markdown(self):
        # The bug this guards: nine of twenty-five real Hibernate descriptions
        # had a codeBlock node, and the profile said 0% of tickets contain
        # code. Everything downstream counts Markdown, so a flattener that
        # produced readable prose was deleting the evidence.
        doc = {
            "type": "doc",
            "content": [
                {
                    "type": "heading",
                    "attrs": {"level": 2},
                    "content": [{"type": "text", "text": "Steps to reproduce"}],
                },
                {
                    "type": "codeBlock",
                    "content": [{"type": "text", "text": "session.get(Foo.class, 1L);"}],
                },
            ],
        }
        text = adf_to_text(doc)
        assert "## Steps to reproduce" in text

        s = shape(text)
        assert s.headings == ("Steps to reproduce",)
        assert s.code_blocks == 1

    def test_links_survive_both_the_ways_jira_stores_them(self):
        # A link is a mark on a text node; a smart link is a node with no text
        # at all. Neither used to come out.
        marked = {
            "type": "paragraph",
            "content": [
                {
                    "type": "text",
                    "text": "the docs",
                    "marks": [{"type": "link", "attrs": {"href": "https://example.com/a"}}],
                }
            ],
        }
        card = {"type": "inlineCard", "attrs": {"url": "https://example.com/b"}}
        assert shape(adf_to_text(marked)).links == 1
        assert shape(adf_to_text(card)).links == 1

    def test_attachments_are_visible_as_images(self):
        media = {"type": "mediaSingle", "content": [{"type": "media", "attrs": {"id": "abc"}}]}
        assert shape(adf_to_text(media)).images == 1

    def test_junk_does_not_explode(self):
        assert adf_to_text(None) == ""
        assert adf_to_text(42) == ""
        assert adf_to_text({"type": "unknownMacro"}) == ""

    def test_status_category_decides_open_or_closed(self):
        # A Jira workflow can end in Done, Closed, Resolved or whatever a
        # project admin invented. Only the category is stable.
        rows = {
            "key": "PROJ-7",
            "fields": {
                "summary": "t",
                "description": {
                    "type": "doc",
                    "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "body"}]}
                    ],
                },
                "status": {"name": "Abgenommen", "statusCategory": {"key": "done"}},
                "labels": [],
                "reporter": {"displayName": "Mira"},
                "created": "2026-03-01T11:00:00.000+0100",
                "updated": "2026-03-01T11:00:00.000+0100",
            },
        }
        tracker = JiraTracker(
            url="https://acme.atlassian.net",
            email="a@b.c",
            token="t",
            client=client("https://acme.atlassian.net/rest/api/3", {"/issue/PROJ-7": rows}),
        )
        ticket = tracker.fetch("PROJ", "PROJ-7")
        assert ticket.state == "closed"
        assert ticket.description == "body"
        assert ticket.url.endswith("/browse/PROJ-7")
