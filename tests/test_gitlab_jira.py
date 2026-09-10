# SPDX-License-Identifier: MIT

"""The GitLab and Jira paths that only ever ran against live instances.

`changed_files` on GitLab and the whole of Jira's `detail` were exercised by
pointing them at real servers and reading the output. That works until it
does not: a response shape changes, and the failure appears on someone else's
board with no test to name it.

Every body below is one of these adapters actually received.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import httpx
import pytest

from ticket_ai_mcp.schemas import LinkedChange, TicketQuery
from ticket_ai_mcp.trackers import TrackerError
from ticket_ai_mcp.trackers.gitlab import GitLabTracker
from ticket_ai_mcp.trackers.jira import JiraTracker


def route(routes: dict[str, object]):
    def handler(request: httpx.Request) -> httpx.Response:
        for suffix, body in routes.items():
            if request.url.path.endswith(suffix):
                if isinstance(body, int):
                    return httpx.Response(body, text="error")
                return httpx.Response(200, json=body)
        return httpx.Response(404, json={"message": f"no route for {request.url.path}"})

    return handler


def gitlab(handler) -> GitLabTracker:
    return GitLabTracker(
        url="https://git.example.com",
        token="t",
        client=httpx.Client(
            base_url="https://git.example.com/api/v4", transport=httpx.MockTransport(handler)
        ),
    )


def jira(handler, **kwargs) -> JiraTracker:
    return JiraTracker(
        url="https://acme.atlassian.net",
        client=httpx.Client(
            base_url="https://acme.atlassian.net/rest/api",
            transport=httpx.MockTransport(handler),
        ),
        **kwargs,
    )


GL_ISSUE = {
    "iid": 42,
    "title": "Etikettendruck bricht ab",
    "description": "## Problem\ntext",
    "state": "closed",
    "labels": ["Bug", "team::lager"],
    "author": {"username": "mira"},
    "assignees": [{"username": "dev1"}],
    "created_at": "2026-03-01T11:00:00+01:00",
    "updated_at": "2026-03-02T11:00:00+01:00",
    "closed_at": "2026-03-02T11:00:00+01:00",
    "web_url": "https://git.example.com/acme/lager/-/issues/42",
}


class TestGitLabChangedFiles:
    def change(self, ref: str = "!7") -> LinkedChange:
        return LinkedChange(ref=ref, title="t", url="u", state="merged", merged=True)

    def test_both_sides_of_a_rename_are_reported(self):
        # Either name is a reasonable place for a reader to start looking, so
        # both are kept.
        body = {
            "changes": [
                {"old_path": "app/old.py", "new_path": "app/new.py"},
                {"old_path": "app/same.py", "new_path": "app/same.py"},
            ]
        }
        found = gitlab(route({"/changes": body})).changed_files("acme/lager", self.change())
        assert found == ("app/new.py", "app/old.py", "app/same.py")

    def test_a_reference_that_is_not_a_number_asks_for_nothing(self):
        called: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            called.append(request.url.path)
            return httpx.Response(200, json={})

        assert gitlab(handler).changed_files("acme/lager", self.change("PROJ-1")) == ()
        assert called == []

    def test_an_endpoint_that_refuses_gives_nothing_rather_than_failing(self):
        # Not every GitLab version and plan serves /changes. A missing one
        # should cost the file list, not the run.
        assert gitlab(route({"/changes": 403})).changed_files("acme/lager", self.change()) == ()

    def test_an_unreachable_host_gives_nothing(self):
        def handler(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route")

        assert gitlab(handler).changed_files("acme/lager", self.change()) == ()


class TestGitLabQuery:
    def test_the_query_carries_state_labels_and_since(self):
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(dict(request.url.params))
            return httpx.Response(200, json=[GL_ISSUE])

        gitlab(handler).search(
            TicketQuery(
                project="acme/lager",
                state="open",
                labels=("Bug", "team::lager"),
                updated_after=datetime(2026, 1, 1, tzinfo=UTC),
                text="etikett",
                limit=5,
            )
        )
        # "open" is not a GitLab state; the API calls it "opened".
        assert seen["state"] == "opened"
        assert seen["labels"] == "Bug,team::lager"
        assert seen["search"] == "etikett"
        assert seen["since"] if "since" in seen else seen["updated_after"].startswith("2026-01-01")

    def test_a_closed_state_passes_through_unchanged(self):
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(dict(request.url.params))
            return httpx.Response(200, json=[])

        gitlab(handler).search(TicketQuery(project="a/b", state="closed", limit=5))
        assert seen["state"] == "closed"

    def test_a_ticket_carries_its_closed_at(self):
        found = gitlab(route({"/issues": [GL_ISSUE]})).search(
            TicketQuery(project="acme/lager", limit=5)
        )
        assert found[0].closed_at is not None
        assert found[0].is_closed


JIRA_ISSUE = {
    "key": "PROJ-7",
    "fields": {
        "summary": "Etikettendruck bricht ab",
        "description": {
            "type": "doc",
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "body"}]}],
        },
        "status": {"name": "Done", "statusCategory": {"key": "done"}},
        "labels": ["bug"],
        "reporter": {"displayName": "Mira"},
        "assignee": {"displayName": "Dev One"},
        "created": "2026-03-01T11:00:00.000+0100",
        "updated": "2026-03-02T11:00:00.000+0100",
        "resolutiondate": "2026-03-02T11:00:00.000+0100",
    },
}


class TestJiraDetail:
    def ticket(self):
        return jira(
            route({"/serverInfo": {"deploymentType": "Cloud"}, "/issue/PROJ-7": JIRA_ISSUE})
        ).fetch("PROJ", "PROJ-7")

    def test_comments_remote_links_and_reopens(self):
        routes = {
            "/serverInfo": {"deploymentType": "Cloud"},
            "/comment": {
                "comments": [
                    {
                        "author": {"displayName": "Jonas"},
                        "body": {
                            "type": "doc",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "welche Spalte?"}],
                                }
                            ],
                        },
                        "created": "2026-03-01T12:00:00.000+0100",
                    }
                ]
            },
            "/remotelink": [
                {"object": {"title": "!93 fix", "url": "https://git.example.com/mr/93"}}
            ],
            "/issue/PROJ-7": {
                **JIRA_ISSUE,
                "changelog": {
                    "histories": [
                        {
                            "items": [
                                {"field": "status", "fromString": "Done", "toString": "In Progress"}
                            ]
                        },
                        {
                            "items": [
                                {
                                    "field": "status",
                                    "fromString": "To Do",
                                    "toString": "In Progress",
                                }
                            ]
                        },
                        {"items": [{"field": "assignee", "fromString": "a", "toString": "b"}]},
                    ]
                },
            },
        }
        tracker = jira(route(routes))
        detail = tracker.detail(tracker.fetch("PROJ", "PROJ-7"))

        assert [c.author for c in detail.comments] == ["Jonas"]
        assert "welche Spalte?" in detail.comments[0].body
        # A remote link is evidence of a change, but never of a merged one:
        # Jira does not say, so `merged` stays False and the ranking treats it
        # as weaker evidence.
        assert detail.linked_changes[0].ref == "!93 fix"
        assert not detail.was_implemented
        # Only the transition out of a done status counts as a reopen.
        assert detail.reopen_count == 1

    def test_remote_links_that_cannot_be_read_cost_only_themselves(self):
        routes = {
            "/serverInfo": {"deploymentType": "Cloud"},
            "/comment": {"comments": []},
            "/remotelink": 404,
            "/issue/PROJ-7": JIRA_ISSUE,
        }
        tracker = jira(route(routes))
        detail = tracker.detail(tracker.fetch("PROJ", "PROJ-7"))
        assert detail.linked_changes == ()

    @pytest.mark.parametrize(
        ("was", "now", "counts"),
        [
            ("Done", "In Progress", True),
            ("Closed", "Reopened", True),
            ("Resolved", "To Do", True),
            ("In Progress", "Done", False),
            ("To Do", "In Progress", False),
            ("Done", "Closed", False),
        ],
    )
    def test_which_transitions_are_reopens(self, was, now, counts):
        # Only status names reach a changelog, so this matches on the handful
        # of words a workflow actually ends with. A workflow whose final
        # status is called something else is missed, which is why a reopen
        # count of zero is weak evidence rather than proof.
        assert (
            JiraTracker._is_reopen({"field": "status", "fromString": was, "toString": now})
            is counts
        )


class TestJiraSearch:
    def test_the_jql_is_built_from_the_query(self):
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/serverInfo"):
                return httpx.Response(200, json={"deploymentType": "Cloud"})
            seen.update(dict(request.url.params))
            return httpx.Response(200, json={"issues": [JIRA_ISSUE]})

        jira(handler).search(
            TicketQuery(
                project="PROJ",
                state="closed",
                labels=("bug",),
                updated_after=datetime(2026, 1, 1, tzinfo=UTC),
                text="etikett",
                limit=10,
            )
        )
        jql = seen["jql"]
        assert 'project = "PROJ"' in jql
        assert 'statusCategory = "done"' in jql
        assert 'labels = "bug"' in jql
        assert 'updated >= "2026-01-01"' in jql
        assert 'text ~ "etikett"' in jql
        assert "ORDER BY updated DESC" in jql

    def test_a_quote_in_the_search_text_is_escaped(self):
        # A bare quote is a JQL syntax error, not a clever query.
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/serverInfo"):
                return httpx.Response(200, json={"deploymentType": "Cloud"})
            seen.update(dict(request.url.params))
            return httpx.Response(200, json={"issues": []})

        jira(handler).search(TicketQuery(project="P", text='say "hi"', limit=5))
        assert '\\"hi\\"' in seen["jql"]

    def test_an_open_state_asks_for_everything_not_done(self):
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/serverInfo"):
                return httpx.Response(200, json={"deploymentType": "Cloud"})
            seen.update(dict(request.url.params))
            return httpx.Response(200, json={"issues": []})

        jira(handler).search(TicketQuery(project="P", state="open", limit=5))
        assert 'statusCategory != "done"' in seen["jql"]

    def test_cloud_paging_follows_the_token(self):
        pages = [
            {"issues": [JIRA_ISSUE], "nextPageToken": "abc"},
            {"issues": [{**JIRA_ISSUE, "key": "PROJ-8"}]},
        ]
        state = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/serverInfo"):
                return httpx.Response(200, json={"deploymentType": "Cloud"})
            body = pages[min(state["n"], len(pages) - 1)]
            state["n"] += 1
            return httpx.Response(200, json=body)

        found = jira(handler).search(TicketQuery(project="P", limit=10))
        assert [t.key for t in found] == ["PROJ-7", "PROJ-8"]


class TestJiraErrors:
    def test_a_missing_issue_names_what_to_check(self):
        with pytest.raises(TrackerError, match="Check the issue key"):
            jira(route({"/serverInfo": {"deploymentType": "Cloud"}})).fetch("PROJ", "PROJ-9")

    def test_an_unreachable_host_is_reported(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/serverInfo"):
                return httpx.Response(200, json={"deploymentType": "Cloud"})
            raise httpx.ConnectError("no route")

        with pytest.raises(TrackerError, match="could not reach"):
            jira(handler).fetch("PROJ", "PROJ-7")

    def test_an_api_choice_has_to_be_one_of_three(self):
        with pytest.raises(TrackerError, match="auto, cloud or server"):
            JiraTracker(url="https://acme.atlassian.net", api="v4")


class TestGitLabAnonymousReads:
    """What gitlab.com actually serves to a caller with no token.

    Both of these came from pointing the adapter at six real public boards.
    The recorded shapes above could not have found either: they are about
    which endpoints answer, not what they answer with.
    """

    def test_a_history_that_needs_a_token_costs_the_history_not_the_ticket(self):
        # gitlab.com serves the issues of a public project to anyone and then
        # answers 401 for that same issue's /notes. Treating that as fatal
        # meant an anonymous run listed forty tickets and read none of them.
        tracker = gitlab(route({"/issues/42": GL_ISSUE, "/notes": 401}))
        detail = tracker.detail(tracker.fetch("acme/lager", "#42"))
        assert detail.ticket.key == "#42"
        assert detail.comments == ()

    def test_what_the_instance_would_not_serve_is_reported_not_shrugged_off(self):
        # Anonymously, gitlab.com gives the issue and its merge requests and
        # withholds the comments. A corpus with no comments cannot tell a
        # ticket that stalled from one a bot closed, so the number that comes
        # out is weaker than it looks - and the reader has to be told.
        tracker = gitlab(route({"/issues/42": GL_ISSUE, "/notes": 401}))
        tracker.anonymous = True
        tracker.detail(tracker.fetch("acme/lager", "#42"))
        from ticket_ai_mcp.messages import render_note

        ((code, params),) = tracker.degradations()
        assert code == "tracker_withheld"
        assert "notes" in params["parts"]
        # The words are made where the words live, and in both languages.
        assert "comments" in render_note(code, params, "en")
        assert "Kommentare" in render_note(code, params, "de")

    def test_an_instance_that_serves_everything_says_nothing(self):
        tracker = gitlab(
            route(
                {
                    "/issues/42": GL_ISSUE,
                    "/notes": [],
                    "/related_merge_requests": [],
                    "/resource_state_events": [],
                }
            )
        )
        tracker.detail(tracker.fetch("acme/lager", "#42"))
        assert tracker.degradations() == ()


class TestJiraBehindAVanityHostname:
    """A Jira that answers at one hostname and lives at another.

    Found on a real instance: issues.redhat.com is a Cloud site behind a
    company hostname, and Atlassian answers 301 to redhat.atlassian.net.
    Browsers follow it, so it is the URL people paste. The adapter did not:
    detection read a 301 as a successful answer, failed to find JSON in it,
    fell back to guessing the product from the hostname, guessed Server
    because the vanity name is not atlassian.net, and asked a Cloud instance
    for a Server endpoint. What the user saw was "answered with something that
    is not JSON" - a sentence about their Jira version.
    """

    def moved(self, home: str = "https://acme.atlassian.net"):
        """A vanity host that redirects everything to its real home."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host != "acme.atlassian.net":
                return httpx.Response(
                    301, headers={"location": f"{home}{request.url.path}"}, text="<html/>"
                )
            if request.url.path.endswith("/serverInfo"):
                return httpx.Response(200, json={"deploymentType": "Cloud", "baseUrl": home})
            return httpx.Response(200, json={"issues": [], "isLast": True})

        return handler

    def vanity(self, **kwargs) -> JiraTracker:
        return JiraTracker(
            url="https://issues.acme.com",
            client=httpx.Client(
                base_url="https://issues.acme.com/rest/api",
                transport=httpx.MockTransport(self.moved()),
            ),
            **kwargs,
        )

    def test_anonymously_it_follows_and_reads_the_right_dialect(self):
        tracker = self.vanity()
        tracker.search(TicketQuery(project="P", limit=5))
        assert tracker._api == "cloud"
        assert tracker.url == "https://acme.atlassian.net"

    def test_with_a_credential_it_refuses_and_names_the_real_url(self):
        # A token belongs to the host it was issued for. Following a redirect
        # with it attached is how a credential ends up somewhere its owner did
        # not choose, so this one asks rather than decides.
        tracker = self.vanity(email="a@b.c", token="secret")
        with pytest.raises(
            TrackerError, match=re.escape("redirects to https://acme.atlassian.net")
        ):
            tracker.search(TicketQuery(project="P", limit=5))

    def test_a_redirect_to_plain_http_is_not_followed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(301, headers={"location": "http://acme.example/rest/api/2/x"})

        tracker = JiraTracker(
            url="https://issues.acme.com",
            client=httpx.Client(
                base_url="https://issues.acme.com/rest/api",
                transport=httpx.MockTransport(handler),
            ),
        )
        # Nothing readable came back, so it falls through to the hostname
        # guess rather than talking to an unencrypted host.
        assert tracker._v == "2"
        assert tracker.url == "https://issues.acme.com"
