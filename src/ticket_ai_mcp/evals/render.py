# SPDX-License-Identifier: MIT

"""The report, written so the weak parts are the ones that stand out.

A table of averages invites reading the biggest number first. The order here is
the opposite: how many runs there were, then how far they spread, then the
average. A mean of 0.81 over nineteen cases that range from 0.4 to 1.0 is a
different claim from the same mean over a tight cluster, and a report that
prints only the mean lets the reader make the stronger claim by accident.

Rates are printed as percentages and counts as counts. `alignment` stays a
fraction because it is the tool's own figure and renaming it in the report
would make two numbers that are the same number look like two figures.
"""

from __future__ import annotations

from .baseline import Verdict
from .metrics import BoardReport, Report, Spread


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def _spread(spread: Spread | None) -> str:
    if spread is None:
        return "-"
    if spread.n == 1:
        # No spread from one observation, and "±0.000" would claim the writer
        # is deterministic.
        return f"{spread.mean:.3f} (n=1)"
    return f"{spread.mean:.3f} ±{spread.stdev:.3f}  [{spread.low:.2f}-{spread.high:.2f}]"


def _board_lines(board: BoardReport) -> list[str]:
    lines = [
        f"  {board.board}",
        f"    runs         {board.runs} over {board.cases} cases"
        + (f", {board.failed} failed" if board.failed else ""),
        f"    alignment    {_spread(board.alignment)}",
    ]
    if board.checks is not None:
        # Printed next to alignment on purpose. 1.000 over two checks and
        # 1.000 over nine are not the same claim, and only this line says so.
        lines.append(
            f"    checks       {board.checks.median:.0f} per draft "
            f"[{board.checks.low:.0f}-{board.checks.high:.0f}]"
        )
    if board.human is not None:
        lines.append(
            f"    human        {board.human.mean:.3f} on this board's own tickets "
            f"[{board.human.low:.2f}-{board.human.high:.2f}]"
        )
    if board.per_case_stdev is not None:
        lines.append(
            f"    same case    ±{board.per_case_stdev.mean:.3f} on average "
            f"(worst ±{board.per_case_stdev.high:.3f})"
        )
    if board.seconds is not None:
        lines.append(f"    seconds      {board.seconds.median:.0f} median, {board.seconds.high:.0f} worst")
    lines.append(f"    revised      {_pct(board.revised)} needed the second pass")
    lines.append(f"    invented     {_pct(board.invented_rate)} of drafts added a heading")
    if board.invented_headings:
        worst = ", ".join(f"{name} ({count})" for name, count in board.invented_headings[:5])
        lines.append(f"                 {worst}")
    if board.wrong_language_rate:
        lines.append(f"    language     {_pct(board.wrong_language_rate)} in the wrong one")
    if board.findings:
        top = ", ".join(f"{code} ({count})" for code, count in board.findings[:5])
        lines.append(f"    findings     {top}")
    return lines


def render(report: Report, verdict: Verdict | None = None) -> str:
    """The whole suite as text, boards first, total last.

    The total comes last on purpose. It is the figure that ends up quoted, and
    putting it under the boards means whoever quotes it has already read the
    board that dragged it down.
    """
    lines = [f"model: {report.model}"]
    if not report.runs:
        lines.append("no runs")
        return "\n".join(lines)

    lines.append("")
    for board in report.boards:
        lines.extend(_board_lines(board))
        lines.append("")

    lines.append(f"  total        {report.runs} runs, {_pct(report.failure_rate)} failed")
    if report.pooled is not None:
        lines.append(f"    pooled     {report.pooled:.3f} over {report.checks} checks")
    lines.append(f"    unweighted {_spread(report.alignment)}")
    if report.findings:
        top = ", ".join(f"{code} ({count})" for code, count in report.findings[:6])
        lines.append(f"    findings   {top}")

    if verdict is not None:
        lines.append("")
        lines.append("  PASS" if verdict.ok else "  FAIL")
        lines.extend(f"    - {complaint}" for complaint in verdict.complaints)
        lines.extend(f"    ({note})" for note in verdict.notes)

    return "\n".join(lines)


def render_markdown(report: Report, verdict: Verdict | None = None) -> str:
    """The same figures as a table, for pasting into a pull request."""
    rows = [
        "| board | runs | alignment | spread | revised | invented | failed |",
        "|---|---|---|---|---|---|---|",
    ]
    for board in report.boards:
        alignment = f"{board.alignment.mean:.3f}" if board.alignment else "-"
        spread = f"±{board.alignment.stdev:.3f}" if board.alignment and board.alignment.n > 1 else "-"
        rows.append(
            f"| {board.board} | {board.runs} | {alignment} | {spread} | "
            f"{_pct(board.revised)} | {_pct(board.invented_rate)} | {board.failed} |"
        )
    total = f"{report.pooled:.3f}" if report.pooled is not None else "-"
    rows.append(
        f"| **total (pooled)** | **{report.runs}** | **{total}** | | | | **{report.failed}** |"
    )

    out = [f"**model:** `{report.model}`", "", *rows]
    if verdict is not None:
        out += ["", "**PASS**" if verdict.ok else "**FAIL**"]
        out += [f"- {complaint}" for complaint in verdict.complaints]
        out += [f"- _{note}_" for note in verdict.notes]
    return "\n".join(out)


HTML_STYLE = """
  :root { color-scheme: light dark; --line: #8884; --bad: #c0392b; --good: #1e8449; }
  body { font: 15px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; margin: 2rem auto;
         max-width: 60rem; padding: 0 1rem; }
  h1 { font-size: 1.2rem; margin: 0 0 .2rem; }
  p.sub { margin: 0 0 1.5rem; opacity: .7; }
  table { border-collapse: collapse; width: 100%; margin: 0 0 1.5rem; }
  th, td { text-align: right; padding: .35rem .6rem; border-bottom: 1px solid var(--line); }
  th:first-child, td:first-child { text-align: left; }
  tr.total td { font-weight: 700; border-top: 2px solid var(--line); border-bottom: none; }
  .fail { color: var(--bad); } .pass { color: var(--good); }
  ul { padding-left: 1.2rem; } li { margin: .2rem 0; }
  .note { opacity: .7; }
"""


def _esc(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_html(report: Report, verdict: Verdict | None = None, *, title: str = "eval") -> str:
    """One self-contained page, because the report has to survive being hosted.

    No stylesheet, no script, no font: the bucket behind CloudFront serves this
    and nothing else, and a page that fetches assets would need a second upload
    and a second cache rule for no gain. `color-scheme` is the whole theme
    handling - the browser picks.
    """
    rows = []
    for board in report.boards:
        alignment = f"{board.alignment.mean:.3f}" if board.alignment else "-"
        spread = (
            f"±{board.alignment.stdev:.3f}"
            if board.alignment and board.alignment.n > 1
            else "-"
        )
        rows.append(
            "<tr>"
            f"<td>{_esc(board.board)}</td><td>{board.runs}</td><td>{alignment}</td>"
            f"<td>{spread}</td><td>{_pct(board.revised)}</td>"
            f"<td>{_pct(board.invented_rate)}</td><td>{board.failed}</td>"
            "</tr>"
        )
    total = f"{report.alignment.mean:.3f}" if report.alignment else "-"
    rows.append(
        f'<tr class="total"><td>total</td><td>{report.runs}</td><td>{total}</td>'
        f"<td></td><td></td><td></td><td>{report.failed}</td></tr>"
    )

    body = [
        f"<h1>{_esc(title)}</h1>",
        f'<p class="sub">model {_esc(report.model)} &middot; {report.runs} runs</p>',
        "<table><thead><tr><th>board</th><th>runs</th><th>alignment</th><th>spread</th>"
        "<th>revised</th><th>invented</th><th>failed</th></tr></thead>",
        "<tbody>" + "".join(rows) + "</tbody></table>",
    ]

    if verdict is not None:
        state = "pass" if verdict.ok else "fail"
        body.append(f'<p class="{state}"><strong>{state.upper()}</strong></p>')
        if verdict.complaints:
            body.append("<ul>" + "".join(f"<li>{_esc(c)}</li>" for c in verdict.complaints) + "</ul>")
        if verdict.notes:
            body.append(
                '<ul class="note">' + "".join(f"<li>{_esc(n)}</li>" for n in verdict.notes) + "</ul>"
            )

    if report.findings:
        body.append("<h1>findings</h1><ul>")
        body.extend(f"<li>{_esc(code)} &times; {count}</li>" for code, count in report.findings)
        body.append("</ul>")

    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_esc(title)}</title><style>{HTML_STYLE}</style></head><body>"
        + "".join(body)
        + "</body></html>\n"
    )
