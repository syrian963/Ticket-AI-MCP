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
