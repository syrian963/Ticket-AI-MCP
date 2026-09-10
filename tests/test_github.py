# SPDX-License-Identifier: MIT

"""The GitHub adapter, past the parts a listing exercises.

This is the adapter most people will point at first, and it was also the least
covered - `detail`, `changed_files`, text search and the error paths were all
running live against real repositories and untested anywhere. Every response
shape below is one this code actually received from api.github.com; the
timeline in particular is the endpoint with the most room to be wrong, because
a cross-reference to a pull request and a cross-reference to another issue look
almost identical.
"""

from __future__ import annotations

import httpx
import pytest

from ticket_ai_mcp.schemas import LinkedChange, Ticket, TicketQuery
from ticket_ai_mcp.trackers import TrackerError
from ticket_ai_mcp.trackers.github import GitHubTracker


def tracker(handler) -> GitHubTracker:
    return GitHubTracker(
        token="t",
        client=httpx.Client(
            base_url="https://api.github.com", transport=httpx.MockTransport(handler)
        ),
    )


def route(routes: dict[str, object], status: int = 200):
    """Serve recorded bodies by path suffix; 404 anything unrouted.

    Unrouted paths 404 on purpose: a test that quietly starts calling a new
    endpoint should fail rather than pass on an empty list.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        for suffix, body in routes.items():
            if request.url.path.endswith(suffix):
                return httpx.Response(status, json=body)
        return httpx.Response(404, json={"message": f"no route for {request.url.path}"})

    return handler


ISSUE = {
    "number": 42,
    "title": "Export drops the last row",
    "body": "## Problem\ntext",
    "state": "open",
    "labels": [{"name": "bug"}, {"name": "p1"}],
    "user": {"login": "mira"},
    "assignees": [{"login": "dev1"}],
    "created_at": "2026-03-01T10:00:00Z",
    "updated_at": "2026-03-02T10:00:00Z",
    "closed_at": None,
    "html_url": "https://github.com/acme/shop/issues/42",
}


class TestConstruction:
    def test_a_token_is_required_and_the_message_names_the_variable(self):
        with pytest.raises(TrackerError, match="TICKET_AI_GITHUB_TOKEN"):
            GitHubTracker(token="")


class TestFetch:
    def test_it_normalises_one_issue(self):
        t = tracker(route({"/issues/42": ISSUE})).fetch("acme/shop", "#42")
        assert t.key == "#42"
        assert t.uid == "github:acme/shop#42"
        assert t.state == "open"
        assert t.labels == ("bug", "p1")
        assert t.author == "mira"
        assert t.assignees == ("dev1",)
        assert t.created_at.hour == 10
        assert t.url.endswith("/issues/42")

    def test_the_hash_is_optional(self):
        assert tracker(route({"/issues/42": ISSUE})).fetch("acme/shop", "42").key == "#42"

    def test_a_missing_issue_says_what_to_check(self):
        with pytest.raises(TrackerError, match="cannot see it"):
            tracker(route({})).fetch("acme/shop", "#42")

    def test_another_error_carries_the_status(self):
        with pytest.raises(TrackerError, match="500"):
            tracker(lambda _: httpx.Response(500, json={"message": "boom"})).fetch("a/b", "#1")


class TestTextSearch:
    def test_a_text_query_uses_the_search_endpoint(self):
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["q"] = request.url.params.get("q", "")
            return httpx.Response(200, json={"items": [ISSUE]})

        found = tracker(handler).search(
            TicketQuery(project="acme/shop", state="open", labels=("bug",), text="export", limit=5)
        )
        assert seen["path"] == "/search/issues"
        # The qualifiers matter: without is:issue the search returns pull
        # requests, which is the same trap as the listing endpoint.
        assert "repo:acme/shop" in seen["q"]
        assert "is:issue" in seen["q"]
        assert "export" in seen["q"]
        assert 'label:"bug"' in seen["q"]
        assert [t.key for t in found] == ["#42"]

    def test_a_failing_search_is_reported(self):
        with pytest.raises(TrackerError, match="422"):
            tracker(lambda _: httpx.Response(422, text="bad query")).search(
                TicketQuery(project="a/b", text="x", limit=5)
            )

    def test_a_rate_limited_search_says_it_is_a_wait(self):
        # A limit arrives as a 403 that looks exactly like a permission
        # problem, and sends people to check a token that was never wrong.
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                403,
                json={"message": "API rate limit exceeded for user ID 1."},
                headers={"x-ratelimit-remaining": "0"},
            )

        with pytest.raises(TrackerError) as caught:
            tracker(handler).search(TicketQuery(project="a/b", text="x", limit=5))
        message = str(caught.value)
        assert "rate limiting" in message
        assert "Nothing is wrong with the token" in message


class TestDetail:
    def timeline_issue(self, events, comments=()):
        return tracker(route({"/comments": list(comments), "/timeline": events}))

    def ticket(self) -> Ticket:
        return tracker(route({"/issues/42": ISSUE})).fetch("acme/shop", "#42")

    def test_comments_and_a_merged_pull_request(self):
        events = [
            {
                "event": "cross-referenced",
                "source": {
                    "issue": {
                        "number": 99,
                        "title": "fix the export",
                        "html_url": "https://github.com/acme/shop/pull/99",
                        "state": "closed",
                        "pull_request": {"merged_at": "2026-03-02T00:00:00Z"},
                    }
                },
            }
        ]
        comments = [
            {
                "user": {"login": "jonas"},
                "body": "which column exactly?",
                "created_at": "2026-03-01T12:00:00Z",
            }
        ]
        detail = self.timeline_issue(events, comments).detail(self.ticket())
        assert [c.author for c in detail.comments] == ["jonas"]
        assert detail.was_implemented
        assert detail.linked_changes[0].ref == "#99"
        assert detail.linked_changes[0].merged

    def test_a_cross_reference_to_an_issue_is_not_a_change(self):
        # The trap: an issue and a pull request are the same shape here, and
        # only the `pull_request` key tells them apart. Counting issues as
        # shipped changes would make every ranking wrong.
        events = [
            {
                "event": "cross-referenced",
                "source": {"issue": {"number": 7, "title": "related", "state": "open"}},
            }
        ]
        assert self.timeline_issue(events).detail(self.ticket()).linked_changes == ()

    def test_an_unmerged_pull_request_is_linked_but_not_shipped(self):
        events = [
            {
                "event": "cross-referenced",
                "source": {
                    "issue": {
                        "number": 8,
                        "title": "wip",
                        "state": "open",
                        "pull_request": {"merged_at": None},
                    }
                },
            }
        ]
        detail = self.timeline_issue(events).detail(self.ticket())
        assert detail.linked_changes and not detail.was_implemented

    def test_reopens_are_counted(self):
        events = [{"event": "reopened"}, {"event": "closed"}, {"event": "reopened"}]
        assert self.timeline_issue(events).detail(self.ticket()).reopen_count == 2

    def test_a_comment_without_a_timestamp_is_dropped_not_crashed_on(self):
        detail = self.timeline_issue([], [{"user": {"login": "x"}, "body": "hi"}]).detail(
            self.ticket()
        )
        assert detail.comments == ()

    def test_a_timeline_the_token_cannot_read_degrades_to_empty(self):
        # Older tokens and some repositories 403 the timeline. That should cost
        # the ranking signal, not the run.
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/timeline"):
                return httpx.Response(403, json={"message": "forbidden"})
            return httpx.Response(200, json=[])

        detail = tracker(handler).detail(self.ticket())
        assert detail.linked_changes == ()
        assert detail.reopen_count == 0

    def test_a_sub_resource_error_that_is_not_a_permission_problem_is_raised(self):
        with pytest.raises(TrackerError, match="502"):
            tracker(lambda _: httpx.Response(502, text="bad gateway")).detail(self.ticket())

    def test_an_unreachable_host_names_the_path(self):
        def handler(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route")

        with pytest.raises(TrackerError, match="could not reach"):
            tracker(handler).detail(self.ticket())


class TestChangedFiles:
    def test_it_returns_the_paths_a_pull_request_touched(self):
        rows = [{"filename": "app/export.py"}, {"filename": "tests/test_export.py"}]
        found = tracker(route({"/files": rows})).changed_files(
            "acme/shop", LinkedChange(ref="#99", title="t", url="u", state="closed", merged=True)
        )
        assert found == ("app/export.py", "tests/test_export.py")

    def test_a_reference_that_is_not_a_number_asks_for_nothing(self):
        # Jira-shaped refs reach here through the shared protocol; asking
        # GitHub for /pulls/PROJ-1/files would 404 on every call.
        called = []

        def handler(request: httpx.Request) -> httpx.Response:
            called.append(request.url.path)
            return httpx.Response(200, json=[])

        found = tracker(handler).changed_files(
            "acme/shop", LinkedChange(ref="PROJ-1", title="t", url="u", state="x", merged=True)
        )
        assert found == ()
        assert called == []

    def test_a_row_without_a_filename_is_skipped(self):
        rows = [{"filename": "a.py"}, {"status": "renamed"}]
        found = tracker(route({"/files": rows})).changed_files(
            "acme/shop", LinkedChange(ref="#1", title="t", url="u", state="x", merged=True)
        )
        assert found == ("a.py",)


class TestBoardsThatAreNotWhatTheyLookLike:
    """Two real repositories, two failures nothing had a message for.

    Both were found by running the tool across thirty public boards. Neither
    is exotic - a project that moved, and a project that turned issues off -
    and both produced an answer that sent the reader somewhere useless.
    """

    def test_a_repository_that_moved_says_so(self):
        # tiangolo/sqlmodel had been transferred, so GitHub answered 301 with
        # a body naming the new location. The old message was "expected a
        # list, got dict", which points at parsing rather than at the fix.
        body = {
            "message": "Moved Permanently",
            "url": "https://api.github.com/repositories/399495186/issues",
            "documentation_url": "https://docs.github.com/rest",
        }
        t = tracker(lambda _: httpx.Response(301, json=body))
        with pytest.raises(TrackerError) as caught:
            t.search(TicketQuery(project="old/name", state="closed", limit=10))
        message = str(caught.value)
        assert "moved" in message.lower()
        assert "TICKET_AI_PROJECT" in message
        assert "399495186" in message

    def test_a_repository_with_issues_switched_off_says_so(self):
        # encode/httpx has has_issues=false, so the issues endpoint serves
        # nothing but pull requests. Silently returning zero tickets sends
        # someone off to build a profile from an empty corpus.
        rows = [
            {"number": i, "title": "a PR", "user": {"login": "x"}, "pull_request": {"url": "u"}}
            for i in range(100)
        ]
        t = tracker(lambda _: httpx.Response(200, json=rows))
        with pytest.raises(TrackerError) as caught:
            t.search(TicketQuery(project="acme/prs-only", state="closed", limit=10))
        assert "no issues" in str(caught.value)
        assert "pull request" in str(caught.value)

    def test_an_empty_repository_is_not_an_error(self):
        # No rows at all is a new or quiet project, which is fine.
        t = tracker(lambda _: httpx.Response(200, json=[]))
        assert t.search(TicketQuery(project="acme/new", state="closed", limit=10)) == []

    def test_a_dict_that_is_not_a_redirect_carries_its_message(self):
        body = {"message": "Repository access blocked", "block": {"reason": "tos"}}
        t = tracker(lambda _: httpx.Response(200, json=body))
        with pytest.raises(TrackerError, match="Repository access blocked"):
            t.search(TicketQuery(project="acme/blocked", limit=10))


class TestListing:
    def test_labels_may_arrive_as_plain_strings(self):
        # The REST API returns objects; some proxies and older responses return
        # bare strings, and either has to come out as a tuple of names.
        row = {**ISSUE, "labels": ["bug", {"name": "p2"}]}
        found = tracker(route({"/issues": [row]})).search(TicketQuery(project="a/b", limit=5))
        assert found[0].labels == ("bug", "p2")

    def test_a_state_and_since_reach_the_query(self):
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(dict(request.url.params))
            return httpx.Response(200, json=[ISSUE])

        from datetime import UTC, datetime

        tracker(handler).search(
            TicketQuery(
                project="a/b",
                state="closed",
                labels=("bug", "p1"),
                updated_after=datetime(2026, 1, 1, tzinfo=UTC),
                limit=5,
            )
        )
        assert seen["state"] == "closed"
        assert seen["labels"] == "bug,p1"
        assert seen["since"].startswith("2026-01-01")


class TestSayingItRatherThanGuessingIt:
    """GitHub reports `has_issues`, so the refusal does not have to infer it.

    Checked against the two repositories in the fleet that reach this line -
    `encode/httpx` and `digitalservicebund/ris-backend-service`. Both are
    `has_issues: false`, so the old inference had been right; this makes it a
    fact, on a path that has already spent ten pages and can afford one call.
    """

    def tracker(self, repo_body, rows):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/repos/acme/shop":
                if repo_body is None:
                    return httpx.Response(404, json={"message": "Not Found"})
                return httpx.Response(200, json=repo_body)
            return httpx.Response(200, json=rows)

        return GitHubTracker(
            token="t",
            client=httpx.Client(
                base_url="https://api.github.com", transport=httpx.MockTransport(handler)
            ),
        )

    @property
    def PRS(self):
        return [
            {"number": n, "title": "a pr", "user": {"login": "x"}, "pull_request": {"url": "u"}}
            for n in range(1, 4)
        ]

    def test_it_says_so_outright_when_github_does(self):
        tracker = self.tracker({"has_issues": False}, self.PRS)
        with pytest.raises(TrackerError, match="has issues disabled - GitHub says so"):
            tracker.search(TicketQuery(project="acme/shop", limit=10))

    def test_it_still_infers_when_the_repository_cannot_be_read(self):
        # A token that can list issues but not read the repository, or an
        # endpoint that answers something else: the message falls back rather
        # than failing, because its whole job is to say something useful.
        tracker = self.tracker(None, self.PRS)
        with pytest.raises(TrackerError, match="most likely has issues disabled"):
            tracker.search(TicketQuery(project="acme/shop", limit=10))

    def test_a_repository_that_answers_nonsense_does_not_crash_the_message(self):
        # Found by a test whose mock answered every path with a list: this
        # helper exists to produce a good message and must not raise while
        # doing it.
        tracker = self.tracker(["not", "an", "object"], self.PRS)
        with pytest.raises(TrackerError, match="most likely has issues disabled"):
            tracker.search(TicketQuery(project="acme/shop", limit=10))

    def test_issues_enabled_but_none_recent_still_gets_the_soft_message(self):
        # A busy repository whose recent closed rows are all pull requests but
        # whose tracker is open. The claim has to stay hedged there.
        tracker = self.tracker({"has_issues": True}, self.PRS)
        with pytest.raises(TrackerError, match="most likely has issues disabled"):
            tracker.search(TicketQuery(project="acme/shop", limit=10))
