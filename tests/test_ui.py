# SPDX-License-Identifier: MIT

"""The web interface, driven over a real socket.

Against a real socket rather than by calling the handler's methods, because
the things that break here are HTTP things: a wrong content length, a query
string nobody parses, a POST body read short. None of those show up when the
handler is poked directly.

Port 0 asks the operating system for a free one, so running the UI in another
terminal does not break the suite.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from conftest import GOOD_BODY, make_detail, make_ticket

from ticket_ai_mcp.config import Settings
from ticket_ai_mcp.i18n import Translator, normalise, ticket_language, ui_language
from ticket_ai_mcp.mining import pick
from ticket_ai_mcp.profile import build
from ticket_ai_mcp.review import Finding, review_draft
from ticket_ai_mcp.trackers import TrackerError
from ticket_ai_mcp.ui import HOST, Handler, page


@pytest.fixture
def profile():
    details = [
        make_detail(
            make_ticket(f"#{i}", description=GOOD_BODY, labels=("bug",), author=f"dev{i % 5}")
        )
        for i in range(20)
    ]
    taken, _ = pick(details, want=20)
    return build(taken, project="acme/shop", tracker="gitlab")


@pytest.fixture
def server(profile):
    bound = type(
        "Bound",
        (Handler,),
        {
            "settings": Settings(tracker="gitlab", project="acme/shop"),
            "profile": profile,
            "language": "en",
        },
    )
    httpd = ThreadingHTTPServer((HOST, 0), bound)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://{HOST}:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def get(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def post(url: str, payload: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


class TestLanguages:
    def test_regional_codes_fold_to_the_language(self):
        assert normalise("de-DE") == "de"
        assert normalise("de_AT") == "de"
        assert normalise("DE") == "de"

    def test_an_unsupported_language_reads_as_english(self):
        # Better plain English than a key nobody can act on.
        assert normalise("fr") == "en"
        assert normalise(None) == "en"

    def test_german_strings_are_actually_german(self):
        assert Translator("de")("tab.draft") == "Entwurf prüfen"
        assert Translator("en")("tab.draft") == "Check a draft"

    def test_a_missing_key_returns_itself(self):
        assert Translator("de")("nope.not.a.key") == "nope.not.a.key"

    def test_ui_and_ticket_language_are_separate_settings(self, monkeypatch):
        # A German team may want English buttons; an English speaker on a
        # German board still writes the ticket in German.
        monkeypatch.setenv("TICKET_AI_UI_LANGUAGE", "en")
        monkeypatch.setenv("TICKET_AI_TICKET_LANGUAGE", "de")
        assert ui_language() == "en"
        assert ticket_language() == "de"

    def test_an_unset_ticket_language_defers_to_the_corpus(self, monkeypatch):
        # A measurement beats a setting: the corpus already knows.
        monkeypatch.delenv("TICKET_AI_TICKET_LANGUAGE", raising=False)
        assert ticket_language() is None


class TestTicketLanguageOverride:
    def test_it_applies_without_relearning(self, profile):
        assert profile.language == "en"
        forced = profile.with_language("de")
        assert forced.language == "de"
        # Nothing else moves.
        assert forced.sections == profile.sections
        assert forced.sample_size == profile.sample_size

    def test_no_override_returns_the_same_profile(self, profile):
        assert profile.with_language(None) is profile


class TestLocalisedFindings:
    """Findings are data until they are displayed, and then they pick a language."""

    def finding(self, profile):
        result = review_draft("t", "kaputt", profile)
        return next(f for f in result.findings if f.code == "missing_section")

    def test_the_same_finding_renders_in_both_languages(self, profile):
        f = self.finding(profile)
        en_what, _, _ = f.localised("en")
        de_what, _, _ = f.localised("de")
        assert en_what != de_what
        assert "Abschnitt" in de_what
        assert "section" in en_what

    def test_the_count_survives_translation(self, profile):
        # The evidence is the point. A German sentence that drops the number is
        # a worse regression than no German sentence.
        f = self.finding(profile)
        for lang in ("en", "de"):
            _, why, _ = f.localised(lang)
            assert "20" in why, f"{lang} lost the count: {why}"

    def test_the_bare_properties_are_english(self, profile):
        # Every caller that has not asked for a language gets English.
        f = self.finding(profile)
        assert f.what == f.localised("en")[0]

    def test_an_unknown_code_returns_itself_rather_than_raising(self):
        from ticket_ai_mcp.review import Finding

        f = Finding(code="not_a_real_code", severity="low")
        assert f.what == "not_a_real_code"

    def test_a_missing_parameter_does_not_take_the_report_down(self):
        # An authoring mistake should read as an obviously wrong sentence, not
        # crash in the middle of a review someone is waiting on.
        from ticket_ai_mcp.review import Finding

        f = Finding(code="missing_section", severity="high", params={})
        assert "{heading}" in f.what or f.what


class TestPage:
    def test_it_ships_its_own_css_and_javascript(self):
        # No CDN and no build step: a local tool that needs either does not
        # get used on the machine that has neither.
        html = page("en")
        assert "<style>" in html and "<script>" in html
        assert "http://" not in html.split("<script>")[0]
        assert "cdn" not in html.lower()

    def test_the_chrome_is_translated(self):
        assert "Entwurf prüfen" in page("de")
        assert "Check a draft" in page("en")

    def test_the_strings_are_valid_json(self):
        # They are interpolated into a script tag; a broken quote there takes
        # the whole page down silently.
        block = page("de").split("const S = ")[1].split(";\n")[0]
        assert json.loads(block)["tab.draft"] == "Entwurf prüfen"


class TestServer:
    def test_the_page_loads(self, server):
        status, html = get(server + "/")
        assert status == 200
        assert "Ticket AI" in html

    def test_the_language_switch_is_a_plain_link(self, server):
        # Server-rendered, so the browser never holds two vocabularies.
        assert "Entwurf prüfen" in get(server + "/?lang=de")[1]
        assert "Check a draft" in get(server + "/?lang=en")[1]

    def test_style_reports_the_profile(self, server):
        status, body = get(server + "/api/style")
        assert status == 200
        data = json.loads(body)
        assert data["sample_size"] == 20
        assert any(s["heading"] == "Acceptance criteria" for s in data["sections"])

    def test_a_draft_is_reviewed(self, server):
        status, data = post(server + "/api/draft", {"title": "x", "description": "kaputt"})
        assert status == 200
        assert data["key"] == "(draft)"
        assert any(f["code"] == "missing_section" for f in data["findings"])
        # The finding still carries its count: the chrome is translated, the
        # evidence is not.
        assert "of the 20 exemplar" in data["findings"][0]["why"]

    def test_a_conforming_draft_comes_back_clean(self, server):
        _, data = post(
            server + "/api/draft",
            {"title": "Fix the label printing", "description": GOOD_BODY, "labels": "bug"},
        )
        assert data["findings"] == []
        assert data["alignment"] == 1.0

    def test_findings_arrive_worst_first(self, server):
        _, data = post(server + "/api/draft", {"title": "x", "description": "kaputt"})
        order = ["high", "medium", "low"]
        seen = [f["severity"] for f in data["findings"]]
        assert seen == sorted(seen, key=order.index)

    def test_labels_are_split_on_commas(self, server):
        _, without = post(server + "/api/draft", {"title": "x", "description": GOOD_BODY})
        _, with_them = post(
            server + "/api/draft",
            {"title": "x", "description": GOOD_BODY, "labels": " bug , urgent "},
        )
        assert any(f["code"] == "no_labels" for f in without["findings"])
        assert not any(f["code"] == "no_labels" for f in with_them["findings"])

    def test_an_unknown_path_is_a_json_404(self, server):
        status, body = get(server + "/wat")
        assert status == 404
        assert json.loads(body)["error"]

    def test_malformed_json_is_a_400(self, server):
        request = urllib.request.Request(
            server + "/api/draft",
            data=b"not json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)
        assert caught.value.code == 400


class TestTheCaveatsFollowThePage:
    """A German page does not explain itself in English.

    The notes say how much of the rest of the page to believe: "built from
    four tickets, treat every rate as a hint", "this instance would not serve
    the comments". They were English literals built in profile.py, which is how
    the most important paragraph on a German page stayed English while every
    heading around it was translated.
    """

    def limited(self):
        from ticket_ai_mcp.mining import score

        detail = make_detail(make_ticket("#1", description=GOOD_BODY))
        return build(
            [score(detail)],
            project="acme/shop",
            tracker="gitlab",
            limits=(
                (
                    "tracker_withheld",
                    {"host": "gitlab.com", "parts": ["notes"], "why": "anonymous"},
                ),
            ),
        )

    def serve(self, profile, language):
        bound = type(
            "Bound",
            (Handler,),
            {
                "settings": Settings(tracker="gitlab", project="acme/shop"),
                "profile": profile,
                "language": language,
            },
        )
        httpd = ThreadingHTTPServer((HOST, 0), bound)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            _, body = get(f"http://{HOST}:{httpd.server_address[1]}/api/style")
            return json.loads(body)
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_the_style_payload_is_in_the_page_language(self):
        joined = " ".join(self.serve(self.limited(), "de")["notes"])
        assert "Kommentare" in joined
        assert "nicht geliefert" in joined
        assert "Aus 1 Tickets" in joined

    def test_and_english_still_reads_as_english(self):
        joined = " ".join(self.serve(self.limited(), "en")["notes"])
        assert "did not serve comments" in joined
        assert "Built from 1 tickets" in joined


class TestNothingEnglishLeftOnAGermanPage:
    """Four things found by opening the page in a browser and reading it.

    Every one of them had passed every unit test in this suite, because the
    tests asked whether the parts were translated and never whether the page
    was. They are all the same defect wearing four hats: a string built as
    English prose somewhere the language was not known yet.
    """

    def profile(self):
        details = [
            make_detail(
                make_ticket(f"#{i}", description=GOOD_BODY, labels=("bug",), author=f"d{i % 5}")
            )
            for i in range(20)
        ]
        taken, _ = pick(details, want=20)
        return build(
            taken,
            project="acme/shop",
            tracker="gitlab",
            limits=(
                (
                    "tracker_withheld",
                    {"host": "gitlab.com", "parts": ["notes"], "why": "anonymous"},
                ),
            ),
        )

    def test_the_habit_rows_are_translated(self):
        # "GEWOHNHEITEN" over "labelled / assigned / list / checklist" - the
        # heading was translated and the rows were the dictionary's own keys.
        page_de = page("de")
        assert '"habit.labelled": "mit Labels"' in page_de
        assert '"habit.cross_ref": "verweist auf ein anderes Ticket"' in page_de

    def test_a_caveat_is_not_styled_as_a_chip(self):
        # The chip style is `text-transform: uppercase`, which shouts a whole
        # paragraph. These are the sentences that say how much of the page to
        # believe, so they get a style that can be read.
        markup = page("de")
        assert 'class="caveat"' in markup
        assert ".caveat {" in markup

    def test_the_review_carries_its_caveats_in_the_page_language(self):
        result = review_draft("Titel", "## Problem\nEs bricht ab.", self.profile())
        german = " ".join(result.localised_caveats("de"))
        assert "nicht geliefert" in german
        assert " ".join(result.localised_caveats("en")) != german

    def test_what_the_ticket_got_right_is_translated_too(self):
        result = review_draft("Titel", GOOD_BODY, self.profile())
        german = " ".join(result.localised_passed("de"))
        assert "hat den Abschnitt" in german
        # The section names are the team's own and stay as the team writes them.
        assert "Problem" in german

    def test_the_language_finding_names_a_language_not_a_code(self):
        # It read "Written in de." in English and "Auf de geschrieben." in
        # German - the template was fine and the parameter was a code.
        f = Finding(
            code="language_mismatch", severity="medium", params={"found": "de", "expected": "en"}
        )
        assert "German" in f.localised("en")[0]
        assert "English" in f.localised("en")[1]
        assert "Deutsch" in f.localised("de")[0]
        assert "Englisch" in f.localised("de")[1]


class TestTheOpenTicketsTab:
    """The third tab, and the only one that talks to the tracker while a
    person is watching.

    Untested until now, including what the page does when the tracker refuses
    mid-session - which is the ordinary case, because a rate limit arrives
    while somebody has the tab open, not before they open it.
    """

    def board(self, profile, tickets, *, refuse: str = ""):
        import ticket_ai_mcp.ui as ui_module

        class Board:
            name = "fake"

            def search(self, query):
                if refuse:
                    raise TrackerError(refuse)
                return tickets

            def fetch(self, project, key):  # pragma: no cover - unused
                raise NotImplementedError

            def detail(self, ticket):  # pragma: no cover - unused
                raise NotImplementedError

        return ui_module, Board()

    def serve(self, profile, tickets, monkeypatch, *, refuse: str = ""):
        ui_module, board = self.board(profile, tickets, refuse=refuse)
        monkeypatch.setattr(ui_module, "tracker_for", lambda _name: board)
        bound = type(
            "Bound",
            (Handler,),
            {
                "settings": Settings(tracker="gitlab", project="acme/shop"),
                "profile": profile,
                "language": "en",
            },
        )
        httpd = ThreadingHTTPServer((HOST, 0), bound)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            return get(f"http://{HOST}:{httpd.server_address[1]}/api/open")
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_it_lists_the_worst_first(self, profile, monkeypatch):
        good = make_ticket("#1", description=GOOD_BODY, state="open", labels=("bug",))
        thin = make_ticket("#2", description="broken", state="open")
        status, body = self.serve(profile, [good, thin], monkeypatch)
        assert status == 200
        rows = json.loads(body)["tickets"]
        assert [r["key"] for r in rows] == ["#2", "#1"]
        assert rows[0]["alignment"] < rows[1]["alignment"]

    def test_a_refusal_mid_session_becomes_an_error_the_page_can_show(self, profile, monkeypatch):
        # The JavaScript renders `d.error` if it is there. A traceback in the
        # terminal and a hung fetch in the browser is the alternative.
        status, body = self.serve(
            profile,
            [],
            monkeypatch,
            refuse="The tracker is rate limiting: 403. Try again in 12 minutes.",
        )
        assert status == 502
        assert "rate limiting" in json.loads(body)["error"]

    def test_a_board_with_nothing_open_is_not_an_error(self, profile, monkeypatch):
        status, body = self.serve(profile, [], monkeypatch)
        assert status == 200
        assert json.loads(body)["tickets"] == []
