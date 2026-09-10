# SPDX-License-Identifier: MIT

"""A local web interface, for the parts of this that want a text box.

Checking a draft on the command line means writing it to a file first, and
nobody drafts a ticket in a file they then have to remember the path of. A page
with a title field, a body field and a button is the right shape for that one
job, so it exists and does not try to be more.

Three decisions worth stating.

**It binds to the loopback address and nothing else.** This process holds a
tracker token. A `--host 0.0.0.0` option would put that token behind whatever
network the laptop is on, and there is no version of that which is a good idea,
so the option is not offered.

**No build step, no CDN, no dependencies.** One HTML string with its CSS and
JavaScript inline. A local dev tool that needs `npm install` before it renders
does not get used, and a page that fetches a framework from a CDN does not work
on the machine that has no route to one.

**Findings are not translated.** The chrome is, because a German team reading
German buttons is the point. The findings carry counts over a named sample and
get pasted verbatim into tickets, so a half-translated sentence with a number
in it would be worse than an English one. `i18n.py` says the same thing at
more length.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import Settings, tracker_for
from .i18n import SUPPORTED, Translator, ui_language
from .profile import Profile
from .review import review as _review
from .review import review_draft as _review_draft
from .schemas import TicketQuery
from .trackers import TrackerError

# Loopback only. See the module docstring: this process holds a token.
HOST = "127.0.0.1"


def _severity_order(value: str) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(value, 3)


class Handler(BaseHTTPRequestHandler):
    # Set by `serve`.
    settings: Settings
    profile: Profile
    language: str

    # The default logs every request to stderr, which buries the one line the
    # user needs - the URL to open.
    def log_message(self, *_: Any) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            # The switcher is a plain link rather than client-side state, so
            # the page always arrives already translated. Two vocabularies in
            # the browser would be one more than the server needs.
            asked = parse_qs(parsed.query).get("lang", [None])[0]
            self._html(page(ui_language(asked or self.language)))
        elif parsed.path == "/api/style":
            self._json(self._style())
        elif parsed.path == "/api/open":
            self._json(self._open())
        else:
            self._error(404, "not found")

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/draft":
            self._error(404, "not found")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, TypeError):
            self._error(400, "expected JSON")
            return

        labels = tuple(
            part.strip() for part in (body.get("labels") or "").split(",") if part.strip()
        )
        result = _review_draft(
            str(body.get("title") or ""),
            str(body.get("description") or ""),
            self.profile,
            labels=labels,
        )
        self._json(_review_payload(result, language=self.language))

    # --- handlers ------------------------------------------------------

    def _style(self) -> dict[str, Any]:
        p = self.profile
        return {
            "project": p.project,
            "tracker": p.tracker,
            "sample_size": p.sample_size,
            "language": p.language,
            # The caveats decide how much of the rest of this page to believe,
            # so they follow the page's language rather than staying English
            # under a German heading.
            "notes": list(p.localised_notes(self.language)),
            "sections": [asdict(s) for s in p.sections],
            "blocks": [
                {
                    "when": c.when_heading,
                    "then": c.then_heading,
                    "count": c.count,
                    "of": c.of,
                    "rate": c.rate,
                    "baseline": c.baseline,
                }
                for c in p.conditionals
                if c.rate >= 0.6
            ],
            "length": {
                "median": round(p.chars_median),
                "p25": round(p.chars_p25),
                "p75": round(p.chars_p75),
            },
            "habits": {
                "labelled": p.label_rate,
                "assigned": p.assignee_rate,
                "list": p.list_rate,
                "checklist": p.checkbox_rate,
                "code": p.code_rate,
                "screenshot": p.image_rate,
                "cross_ref": p.cross_ref_rate,
            },
            "labels": [{"name": n, "rate": r} for n, r in p.common_labels[:10]],
            "exemplars": list(p.exemplar_keys),
            # Built here rather than in the page, so the button offers exactly
            # what the MCP tool offers. Computed in the browser it drifted
            # immediately: it used the board-wide rates alone and came back
            # empty on a board whose template lives in a conditional block.
            "skeleton": [{"heading": h, "why": w} for h, w in p.skeleton()],
        }

    def _open(self) -> dict[str, Any]:
        tracker = tracker_for(self.settings.tracker)
        tickets = tracker.search(
            TicketQuery(project=self.settings.project, state="open", limit=100)
        )
        rows = [
            _review_payload(_review(t, self.profile), url=t.url, language=self.language)
            for t in tickets
        ]
        rows.sort(key=lambda r: (r["alignment"] is None, r["alignment"] or 0.0, r["key"]))
        return {"tickets": rows}

    # --- plumbing ------------------------------------------------------

    def _html(self, text: str) -> None:
        payload = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, data: dict[str, Any]) -> None:
        try:
            payload = json.dumps(data).encode("utf-8")
        except (TypeError, ValueError) as exc:
            self._error(500, str(exc))
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _error(self, code: int, message: str) -> None:
        payload = json.dumps({"error": message}).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def handle_one_request(self) -> None:
        # A tracker that will not answer is an error page, not a traceback in
        # the terminal and a hung fetch in the browser.
        try:
            super().handle_one_request()
        except TrackerError as exc:
            try:
                self._error(502, str(exc))
            except OSError:
                pass


def _review_payload(result, *, url: str = "", language: str | None = None) -> dict[str, Any]:
    """A review as JSON, with the findings rendered in the reader's language.

    `asdict` will not do here, and that is the point: a finding is a code and
    its measurements, and the sentence is built on the way out. It is why a
    German board can now show German findings rather than German chrome around
    English prose.
    """
    findings = [
        dict(
            zip(
                ("what", "why", "fix"),
                f.localised(language),
                strict=True,
            ),
            code=f.code,
            severity=f.severity,
        )
        for f in sorted(result.findings, key=lambda f: _severity_order(f.severity))
    ]
    return {
        "key": result.ticket_key,
        "url": url or result.ticket_url,
        "alignment": result.alignment,
        "checks_run": result.checks_run,
        "findings": findings,
        "passed": list(result.localised_passed(language)),
        "caveats": list(result.localised_caveats(language)),
    }


def serve(
    settings: Settings, profile: Profile, *, port: int = 8760, language: str | None = None
) -> str:
    """Start the server and return the URL it is on.

    Blocks. Port 0 asks the operating system for a free one, which is what the
    tests use so a developer already running the UI does not break them.
    """
    lang = ui_language(language)
    handler = type(
        "BoundHandler",
        (Handler,),
        {"settings": settings, "profile": profile, "language": lang},
    )
    server = ThreadingHTTPServer((HOST, port), handler)
    url = f"http://{HOST}:{server.server_address[1]}"
    print(f"ticket-ai ui on {url}  ({lang})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return url


def page(language: str) -> str:
    """The whole interface: one file, no dependencies."""
    t = Translator(language)
    strings = {
        key: t(key)
        for key in (
            "app.title",
            "app.tagline",
            "tab.style",
            "tab.draft",
            "tab.open",
            "style.none",
            "style.template",
            "style.blocks",
            "style.length",
            "style.habits",
            "habit.labelled",
            "habit.assigned",
            "habit.list",
            "habit.checklist",
            "habit.code",
            "habit.screenshot",
            "habit.cross_ref",
            "style.labels",
            "style.language",
            "style.measured",
            "draft.title",
            "draft.title.hint",
            "draft.body",
            "draft.body.hint",
            "draft.labels",
            "draft.labels.hint",
            "draft.check",
            "draft.checking",
            "draft.empty",
            "draft.template",
            "review.alignment",
            "review.findings",
            "review.passed",
            "review.clean",
            "review.fix",
            "review.unmeasurable",
            "open.load",
            "open.loading",
            "open.worst",
            "open.clean",
            "open.none",
            "severity.high",
            "severity.medium",
            "severity.low",
            "error",
            "note.alignment",
        )
    }
    strings["review.checks"] = t("review.checks", n="{n}")
    strings["style.sample"] = t("style.sample", n="{n}")

    return (
        _TEMPLATE.replace("__LANG__", language)
        .replace("__STRINGS__", json.dumps(strings, ensure_ascii=False))
        .replace(
            "__SWITCH__",
            "".join(
                f'<a href="/?" data-lang="{code}" class="lang{" on" if code == language else ""}">'
                f"{code.upper()}</a>"
                for code in SUPPORTED
            ),
        )
    )


_TEMPLATE = """<!doctype html>
<html lang="__LANG__">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ticket AI</title>
<style>
:root {
  color-scheme: light dark;
  --bg: #fbfaf8; --panel: #ffffff; --ink: #1a1a19; --muted: #6b6a66;
  --line: #e3e0da; --accent: #3a5a9b; --high: #b3261e; --mid: #9a6700;
  --low: #57534e; --ok: #2e6b3f; --radius: 10px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #17171a; --panel: #1f1f23; --ink: #eceae6; --muted: #9b9992;
    --line: #33333a; --accent: #8fb0e8; --high: #f29d97; --mid: #e6c069;
    --low: #a8a49d; --ok: #86d19d;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
}
header {
  border-bottom: 1px solid var(--line); padding: 18px 20px;
  display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap;
}
h1 { font-size: 17px; margin: 0; letter-spacing: -0.01em; }
.tagline { color: var(--muted); font-size: 13px; margin-right: auto; }
.lang {
  color: var(--muted); text-decoration: none; font-size: 12px;
  padding: 3px 7px; border-radius: 6px; border: 1px solid transparent;
}
.lang.on { color: var(--ink); border-color: var(--line); background: var(--panel); }
nav { display: flex; gap: 4px; padding: 12px 20px 0; flex-wrap: wrap; }
nav button {
  font: inherit; font-size: 13px; cursor: pointer; color: var(--muted);
  background: none; border: 1px solid transparent; border-bottom: none;
  padding: 8px 14px; border-radius: var(--radius) var(--radius) 0 0;
}
nav button[aria-selected="true"] {
  color: var(--ink); background: var(--panel);
  border-color: var(--line); margin-bottom: -1px;
}
main { padding: 0 20px 60px; }
.panel {
  background: var(--panel); border: 1px solid var(--line);
  border-radius: 0 var(--radius) var(--radius) var(--radius);
  padding: 22px; max-width: 900px;
}
h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .07em;
     color: var(--muted); margin: 26px 0 10px; font-weight: 600; }
h2:first-child { margin-top: 0; }
.rows { display: grid; gap: 6px; }
.row { display: flex; align-items: baseline; gap: 10px; }
.row .name { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.bar { width: 90px; height: 5px; border-radius: 3px; background: var(--line);
       overflow: hidden; flex: none; }
.bar i { display: block; height: 100%; background: var(--accent); }
.num { color: var(--muted); font-size: 12px; min-width: 62px; text-align: right;
       font-variant-numeric: tabular-nums; flex: none; }
label { display: block; font-size: 12px; color: var(--muted); margin: 14px 0 4px; }
input, textarea {
  width: 100%; font: inherit; padding: 9px 11px; color: var(--ink);
  background: var(--bg); border: 1px solid var(--line); border-radius: 8px;
}
textarea { min-height: 240px; resize: vertical;
           font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
           font-size: 13px; }
input:focus, textarea:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
.actions { display: flex; gap: 8px; align-items: center; margin-top: 14px; }
button.go {
  font: inherit; cursor: pointer; padding: 9px 18px; border-radius: 8px;
  border: 1px solid var(--accent); background: var(--accent); color: #fff;
}
button.ghost { background: none; color: var(--muted); border-color: var(--line); }
button:disabled { opacity: .55; cursor: default; }
.score { font-size: 30px; font-weight: 600; letter-spacing: -0.02em; }
.score small { font-size: 13px; font-weight: 400; color: var(--muted);
               margin-left: 8px; letter-spacing: 0; }
.finding { border-left: 3px solid var(--line); padding: 2px 0 2px 14px; margin: 14px 0; }
.finding.high { border-color: var(--high); }
.finding.medium { border-color: var(--mid); }
.finding.low { border-color: var(--low); }
.finding .what { font-weight: 600; }
.finding .why { color: var(--muted); font-size: 13px; margin-top: 3px; }
.finding .fix { font-size: 13px; margin-top: 5px; }
.tag { font-size: 10px; text-transform: uppercase; letter-spacing: .08em;
       color: var(--muted); }
/* A caveat is a sentence and has to read like one. These used to carry the
   chip style above, which shouts a whole paragraph in capitals - and they are
   the sentences that say how much of the rest of the page to believe. */
.caveat { font-size: 12px; color: var(--muted); line-height: 1.5;
          margin: 10px 0 0; }
.ok { color: var(--ok); }
.note { color: var(--muted); font-size: 12px; border-top: 1px solid var(--line);
        margin-top: 26px; padding-top: 12px; }
.err { color: var(--high); }
ul.plain { list-style: none; padding: 0; margin: 0; display: grid; gap: 4px;
           font-size: 13px; }
ul.plain li::before { content: "✓ "; color: var(--ok); }
.tickets { display: grid; gap: 2px; }
.ticket { display: flex; gap: 12px; align-items: baseline; padding: 7px 0;
          border-bottom: 1px solid var(--line); }
.ticket a { color: var(--accent); text-decoration: none; font-variant-numeric: tabular-nums; }
.ticket .top { flex: 1; color: var(--muted); font-size: 13px; overflow-wrap: anywhere; }
.hidden { display: none; }
code { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; }
</style>
</head>
<body>
<header>
  <h1 id="h-title"></h1>
  <span class="tagline" id="h-tagline"></span>
  <span id="switch">__SWITCH__</span>
</header>

<nav role="tablist">
  <button role="tab" data-tab="style" aria-selected="true"></button>
  <button role="tab" data-tab="draft" aria-selected="false"></button>
  <button role="tab" data-tab="open" aria-selected="false"></button>
</nav>

<main>
  <section class="panel" id="p-style"></section>

  <section class="panel hidden" id="p-draft">
    <label for="t"></label>
    <input id="t" autocomplete="off">
    <div class="tag" id="t-hint"></div>
    <label for="b"></label>
    <textarea id="b" spellcheck="false"></textarea>
    <div class="tag" id="b-hint"></div>
    <label for="l"></label>
    <input id="l" autocomplete="off">
    <div class="tag" id="l-hint"></div>
    <div class="actions">
      <button class="go" id="check"></button>
      <button class="ghost" id="tpl"></button>
    </div>
    <div id="draft-out"></div>
  </section>

  <section class="panel hidden" id="p-open">
    <div class="actions"><button class="go" id="load"></button></div>
    <div id="open-out"></div>
  </section>

  <p class="note" id="note"></p>
</main>

<script>
const S = __STRINGS__;
const pct = v => Math.round(v * 100) + "%";
const esc = s => String(s ?? "").replace(/[&<>"]/g, c =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

// Language is a query parameter so the switcher is a plain link, and the
// server renders the strings. Nothing here needs to know two vocabularies.
for (const a of document.querySelectorAll(".lang")) {
  a.href = "/?lang=" + a.dataset.lang;
}

document.getElementById("h-title").textContent = S["app.title"];
document.getElementById("h-tagline").textContent = S["app.tagline"];
document.getElementById("note").textContent = S["note.alignment"];
for (const b of document.querySelectorAll("nav button")) {
  b.textContent = S["tab." + b.dataset.tab];
  b.onclick = () => {
    for (const o of document.querySelectorAll("nav button"))
      o.setAttribute("aria-selected", String(o === b));
    for (const p of ["style", "draft", "open"])
      document.getElementById("p-" + p).classList.toggle("hidden", p !== b.dataset.tab);
  };
}
const setText = (id, key) => document.getElementById(id).textContent = S[key];
document.querySelector('label[for="t"]').textContent = S["draft.title"];
document.querySelector('label[for="b"]').textContent = S["draft.body"];
document.querySelector('label[for="l"]').textContent = S["draft.labels"];
setText("t-hint", "draft.title.hint");
setText("b-hint", "draft.body.hint");
setText("l-hint", "draft.labels.hint");
setText("check", "draft.check");
setText("tpl", "draft.template");
setText("load", "open.load");

let TEMPLATE = "";

function bar(rate) {
  return `<span class="bar"><i style="width:${Math.round(rate * 100)}%"></i></span>`;
}

function renderStyle(d) {
  const box = document.getElementById("p-style");
  if (!d.sample_size) {
    box.innerHTML = `<p class="err">${esc(S["style.none"])}</p>`;
    return;
  }
  const h = [];
  h.push(`<h2>${esc(d.project)}</h2>`);
  h.push(`<p>${esc(S["style.sample"].replace("{n}", d.sample_size))}.</p>`);

  // The server decides what the skeleton is. Recomputed here from the rates
  // alone it left the button dead on a board whose template lives in a
  // conditional block rather than in any board-wide rate.
  TEMPLATE = (d.skeleton || []).map(s => "## " + s.heading + "\\n\\n").join("");
  document.getElementById("tpl").disabled = !TEMPLATE;

  const shown = d.sections.filter(s => s.rate >= 0.2);
  if (shown.length) {
    h.push(`<h2>${esc(S["style.template"])}</h2><div class="rows">`);
    for (const s of shown) {
      h.push(`<div class="row"><span class="name">${esc(s.heading)}</span>` +
        bar(s.rate) + `<span class="num">${s.count}/${d.sample_size}</span></div>`);
    }
    h.push("</div>");
  }

  if (d.blocks.length) {
    h.push(`<h2>${esc(S["style.blocks"])}</h2><div class="rows">`);
    for (const b of d.blocks) {
      h.push(`<div class="row"><span class="name">${esc(b.when)} → ${esc(b.then)}` +
        `</span><span class="num">${b.count}/${b.of}</span></div>`);
    }
    h.push("</div>");
  }

  h.push(`<h2>${esc(S["style.length"])}</h2>`);
  h.push(`<div class="rows"><div class="row"><span class="name">` +
    `${d.length.p25} - ${d.length.median} - ${d.length.p75}</span></div></div>`);

  h.push(`<h2>${esc(S["style.habits"])}</h2><div class="rows">`);
  for (const [k, v] of Object.entries(d.habits)) {
    const label = S["habit." + k] || k.replace("_", " ");
    h.push(`<div class="row"><span class="name">${esc(label)}</span>` +
      bar(v) + `<span class="num">${pct(v)}</span></div>`);
  }
  h.push("</div>");

  if (d.labels.length) {
    h.push(`<h2>${esc(S["style.labels"])}</h2><div class="rows">`);
    for (const l of d.labels) {
      h.push(`<div class="row"><span class="name"><code>${esc(l.name)}</code></span>` +
        bar(l.rate) + `<span class="num">${pct(l.rate)}</span></div>`);
    }
    h.push("</div>");
  }

  if (d.language) {
    h.push(`<h2>${esc(S["style.language"])}</h2><p>${esc(d.language.toUpperCase())}</p>`);
  }
  for (const n of d.notes) h.push(`<p class="caveat">${esc(n)}</p>`);
  box.innerHTML = h.join("");
}

function renderReview(r) {
  const h = [];
  const score = r.alignment === null
    ? `<span class="score">-</span><small>${esc(S["review.unmeasurable"])}</small>`
    : `<span class="score">${pct(r.alignment)}` +
      `<small>${esc(S["review.checks"].replace("{n}", r.checks_run))}</small></span>`;
  h.push(`<h2>${esc(S["review.alignment"])}</h2><p>${score}</p>`);
  for (const c of r.caveats) h.push(`<p class="caveat">${esc(c)}</p>`);

  if (!r.findings.length) {
    h.push(`<p class="ok">${esc(S["review.clean"])}</p>`);
  } else {
    h.push(`<h2>${esc(S["review.findings"])}</h2>`);
    for (const f of r.findings) {
      h.push(`<div class="finding ${esc(f.severity)}">` +
        `<div class="tag">${esc(S["severity." + f.severity] || f.severity)}</div>` +
        `<div class="what">${esc(f.what)}</div>` +
        `<div class="why">${esc(f.why)}</div>` +
        `<div class="fix">${esc(S["review.fix"])}: ${esc(f.fix)}</div></div>`);
    }
  }
  if (r.passed.length) {
    h.push(`<h2>${esc(S["review.passed"])}</h2><ul class="plain">` +
      r.passed.map(p => `<li>${esc(p)}</li>`).join("") + "</ul>");
  }
  return h.join("");
}

document.getElementById("tpl").onclick = () => {
  const b = document.getElementById("b");
  if (TEMPLATE && !b.value.trim()) b.value = TEMPLATE;
  b.focus();
};

document.getElementById("check").onclick = async () => {
  const btn = document.getElementById("check");
  const out = document.getElementById("draft-out");
  const title = document.getElementById("t").value;
  const description = document.getElementById("b").value;
  if (!title.trim() && !description.trim()) {
    out.innerHTML = `<p class="err">${esc(S["draft.empty"])}</p>`;
    return;
  }
  btn.disabled = true; btn.textContent = S["draft.checking"];
  try {
    const res = await fetch("/api/draft", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({title, description, labels: document.getElementById("l").value}),
    });
    const d = await res.json();
    out.innerHTML = d.error ? `<p class="err">${esc(d.error)}</p>` : renderReview(d);
  } catch (e) {
    out.innerHTML = `<p class="err">${esc(S["error"])}: ${esc(e.message)}</p>`;
  } finally {
    btn.disabled = false; btn.textContent = S["draft.check"];
  }
};

document.getElementById("load").onclick = async () => {
  const btn = document.getElementById("load");
  const out = document.getElementById("open-out");
  btn.disabled = true; btn.textContent = S["open.loading"];
  try {
    const res = await fetch("/api/open");
    const d = await res.json();
    if (d.error) { out.innerHTML = `<p class="err">${esc(d.error)}</p>`; return; }
    if (!d.tickets.length) { out.innerHTML = `<p>${esc(S["open.none"])}</p>`; return; }
    const rows = d.tickets.map(t => {
      const top = t.findings.length ? t.findings[0].what : S["open.clean"];
      const score = t.alignment === null ? "-" : pct(t.alignment);
      const link = t.url
        ? `<a href="${esc(t.url)}" target="_blank" rel="noreferrer">${esc(t.key)}</a>`
        : esc(t.key);
      return `<div class="ticket">${link}<span class="num">${score}</span>` +
             `<span class="top">${esc(top)}</span></div>`;
    });
    out.innerHTML = `<h2>${esc(S["open.worst"])}</h2><div class="tickets">` +
                    rows.join("") + "</div>";
  } catch (e) {
    out.innerHTML = `<p class="err">${esc(S["error"])}: ${esc(e.message)}</p>`;
  } finally {
    btn.disabled = false; btn.textContent = S["open.load"];
  }
};

fetch("/api/style").then(r => r.json()).then(renderStyle).catch(e => {
  document.getElementById("p-style").innerHTML =
    `<p class="err">${esc(S["error"])}: ${esc(e.message)}</p>`;
});
</script>
</body>
</html>
"""
