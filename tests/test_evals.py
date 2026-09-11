# SPDX-License-Identifier: MIT

"""The harness, checked the way the rest of the tool is: without a model.

A fake writer returns a fixed string, so these tests say nothing about draft
quality - that is what the dataset is for. What they do say is that a broken
case file is caught with the line number on it, that a failing model takes one
case down rather than the suite, and that a result file survives being written
and read back.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from ticket_ai_mcp.cli import main
from ticket_ai_mcp.evals.baseline import Baseline, compare
from ticket_ai_mcp.evals.calibration import (
    Label,
    agreement,
    cohens_kappa,
    load_labels,
    render_agreement,
)
from ticket_ai_mcp.evals.dataset import (
    Board,
    Case,
    DatasetError,
    jsonl_lines,
    load_board,
    load_suite,
    write_board,
)
from ticket_ai_mcp.evals.judge import Verdict as JudgeVerdict
from ticket_ai_mcp.evals.judge import judge_runs, parse
from ticket_ai_mcp.evals.metrics import (
    Spread,
    ceiling,
    invented_sections,
    score,
    score_board,
    skeleton_coverage,
    wrong_language,
)
from ticket_ai_mcp.evals.render import render, render_html, render_markdown
from ticket_ai_mcp.evals.runner import CaseRun, read_runs, run_case, run_suite, write_runs
from ticket_ai_mcp.profile import Profile
from ticket_ai_mcp.writers.base import WriterError


def _profile() -> Profile:
    return Profile(
        project="acme/shop",
        tracker="github",
        built_at="2026-09-11",
        sample_size=20,
        exemplar_keys=("#1", "#2"),
        chars_median=400,
        language="en",
    )


def _case(key: str = "#7") -> Case:
    return Case(
        key=key,
        title="Label printing stops after ten positions",
        url=f"https://github.com/acme/shop/issues/{key.lstrip('#')}",
        reference="What happens now\nIt stops.\n",
        labels=("bug",),
    )


def _board(cases: tuple[Case, ...] = ()) -> Board:
    return Board(
        slug="acme",
        tracker="github",
        project="acme/shop",
        url="https://github.com/acme/shop",
        fetched_at="2026-09-11",
        profile=_profile(),
        cases=cases or (_case(),),
    )


class FixedWriter:
    name = "fixed"
    model = "fixed-1"

    def __init__(self, text: str = "It stops after ten.") -> None:
        self.text = text
        self.calls = 0

    def write(self, system: str, prompt: str) -> str:
        self.calls += 1
        return self.text


class BrokenWriter:
    name = "broken"
    model = "broken-1"

    def write(self, system: str, prompt: str) -> str:
        raise WriterError("no model at http://localhost:11434")


def _write_board(tmp_path, *, cases_text: str | None = None):
    directory = tmp_path / "acme"
    directory.mkdir()
    (directory / "board.json").write_text(
        json.dumps(
            {
                "tracker": "github",
                "project": "acme/shop",
                "url": "https://github.com/acme/shop",
                "fetched_at": "2026-09-11",
            }
        ),
        encoding="utf-8",
    )
    (directory / "profile.json").write_text(_profile().to_json(), encoding="utf-8")
    (directory / "cases.jsonl").write_text(
        cases_text
        if cases_text is not None
        else json.dumps(_case().to_dict(), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return directory


# --------------------------------------------------------------------- dataset


def test_load_board_round_trips_a_case(tmp_path):
    board = load_board(_write_board(tmp_path))
    assert board.slug == "acme"
    assert board.language == "en"
    assert [c.key for c in board.cases] == ["#7"]
    assert board.cases[0].labels == ("bug",)


def test_a_case_without_a_public_url_is_refused(tmp_path):
    bad = dict(_case().to_dict(), url="internal://board/42")
    directory = _write_board(tmp_path, cases_text=json.dumps(bad) + "\n")
    with pytest.raises(DatasetError, match="no public url"):
        load_board(directory)


def test_a_duplicate_key_names_the_line(tmp_path):
    line = json.dumps(_case().to_dict())
    directory = _write_board(tmp_path, cases_text=f"{line}\n{line}\n")
    with pytest.raises(DatasetError, match=r"cases\.jsonl:2: duplicate case key #7"):
        load_board(directory)


def test_broken_json_names_the_line(tmp_path):
    directory = _write_board(tmp_path, cases_text='{"key": "#1"\n')
    with pytest.raises(DatasetError, match=r"cases\.jsonl:1: not valid JSON"):
        load_board(directory)


def test_an_empty_cases_file_is_an_error(tmp_path):
    directory = _write_board(tmp_path, cases_text="\n\n")
    with pytest.raises(DatasetError, match="has no cases"):
        load_board(directory)


def test_load_suite_is_ordered_and_skips_non_boards(tmp_path):
    import shutil

    acme = _write_board(tmp_path)
    shutil.copytree(acme, tmp_path / "zeta")
    # A directory with no board.json is not half a board, it is not a board.
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "todo.md").write_text("later", encoding="utf-8")
    assert [b.slug for b in load_suite(tmp_path)] == ["acme", "zeta"]


def test_a_board_missing_a_field_says_which_one(tmp_path):
    directory = _write_board(tmp_path)
    (directory / "board.json").write_text(json.dumps({"tracker": "github"}), encoding="utf-8")
    with pytest.raises(DatasetError, match="missing project"):
        load_board(directory)


def test_a_missing_profile_is_not_an_empty_profile(tmp_path):
    # Profile.from_json on nothing would be a board that scores every draft
    # against no expectations at all, which reads as a perfect result.
    directory = _write_board(tmp_path)
    (directory / "profile.json").unlink()
    with pytest.raises(DatasetError, match="does not exist"):
        load_board(directory)


def test_write_board_produces_what_load_board_reads(tmp_path):
    directory = tmp_path / "acme"
    write_board(
        directory,
        tracker="github",
        project="acme/shop",
        url="https://github.com/acme/shop",
        fetched_at="2026-09-11",
        profile=_profile(),
        cases=[_case("#1"), _case("#2")],
    )
    board = load_board(directory)
    assert [c.key for c in board.cases] == ["#1", "#2"]
    assert board.profile.chars_median == _profile().chars_median


def test_an_empty_dataset_directory_is_an_error(tmp_path):
    with pytest.raises(DatasetError, match="contains no boards"):
        load_suite(tmp_path)


# ---------------------------------------------------------------------- runner


def test_run_case_records_the_model_that_answered():
    run = run_case(_board(), _case(), FixedWriter())
    assert run.ok
    assert run.model == "fixed-1"
    assert run.alignment is not None
    assert run.seconds >= 0


def test_a_writer_failure_is_a_record_not_a_crash():
    run = run_case(_board(), _case(), BrokenWriter())
    assert not run.ok
    assert "no model" in run.error
    # Not 0.0: an outage is not a score of zero, and averaging it in would
    # report an unreachable model as a quality problem.
    assert run.alignment is None


def test_run_suite_repeats_every_case_and_numbers_the_repeats():
    board = _board(cases=(_case("#1"), _case("#2")))
    runs = list(run_suite([board], FixedWriter(), repeats=3))
    assert len(runs) == 6
    assert sorted({r.repeat for r in runs}) == [0, 1, 2]
    # Repeats are the outer loop: the whole board is seen once before any case
    # is seen twice.
    assert [r.case for r in runs] == ["#1", "#2", "#1", "#2", "#1", "#2"]


def test_progress_reports_before_and_after_each_case():
    seen = []
    list(run_suite([_board()], FixedWriter(), on_progress=seen.append))
    assert [p.run is None for p in seen] == [True, False]
    assert seen[-1].done == seen[-1].total == 1


def test_results_survive_a_round_trip(tmp_path):
    path = tmp_path / "runs.jsonl"
    original = list(run_suite([_board()], FixedWriter()))
    assert write_runs(path, original) == 1
    back = read_runs(path)
    # Field for field, not "a CaseRun came back". `findings` in particular:
    # JSON has no tuple, and a list would compare unequal and break any later
    # grouping that assumes it is hashable.
    assert back == original
    assert isinstance(back[0], CaseRun)
    assert isinstance(back[0].findings, tuple)


def test_write_runs_appends_so_an_interrupted_suite_keeps_its_results(tmp_path):
    path = tmp_path / "runs.jsonl"
    write_runs(path, run_suite([_board()], FixedWriter()))
    write_runs(path, run_suite([_board()], FixedWriter()))
    assert len(read_runs(path)) == 2


# ------------------------------------------------------------ collection tool


def test_board_url_handles_gitlabs_dash_separator():
    from tools.build_eval_dataset import board_url

    assert (
        board_url("https://gitlab.com/inkscape/inkscape/-/work_items/3033")
        == "https://gitlab.com/inkscape/inkscape"
    )
    assert (
        board_url("https://github.com/pydantic/pydantic/issues/42")
        == "https://github.com/pydantic/pydantic"
    )


def test_the_split_is_stable_across_processes():
    from tools.build_eval_dataset import _bucket

    # Not `hash()`: that is salted per process, so a dataset rebuilt tomorrow
    # would split differently and the two would not be comparable.
    assert _bucket("gitlab:inkscape/inkscape:3033") == _bucket("gitlab:inkscape/inkscape:3033")
    assert _bucket("a") != _bucket("b")


# --------------------------------------------------------------------- metrics


def _run(case="#7", *, alignment=0.8, body="Summary\nIt stops.", attempts=1, seconds=1.0, **kw):
    return CaseRun(
        board="acme",
        case=case,
        model="fixed-1",
        repeat=kw.pop("repeat", 0),
        seconds=seconds,
        attempts=attempts,
        alignment=alignment,
        checks_run=5,
        body=body,
        **kw,
    )


def _board_with_sections(*names):
    from ticket_ai_mcp.profile import Section

    sections = tuple(
        Section(heading=n, key=n.lower(), count=9, rate=0.9, position=i)
        for i, n in enumerate(names)
    )
    # `replace`, not `Profile(**profile.__dict__)`: these dataclasses use
    # slots, so there is no __dict__ to unpack.
    return replace(_board(), profile=replace(_profile(), sections=sections))


def test_a_heading_the_board_uses_is_not_invented():
    board = _board_with_sections("Summary", "Steps to reproduce")
    assert invented_sections("## Summary\ntext\n## Steps to reproduce\nmore", board) == ()


def test_a_heading_the_board_never_used_is_invented():
    board = _board_with_sections("Summary")
    found = invented_sections("## Summary\ntext\n## Impact\nbig", board)
    assert found == ("Impact",)


def test_invented_is_measured_against_every_section_not_the_skeleton():
    # A heading used in a third of tickets is house style even though the
    # prompt never asked for it. Counting it would punish a draft for being
    # right.
    from ticket_ai_mcp.profile import Section

    board = _board_with_sections("Summary")
    rare = Section(heading="Workaround", key="workaround", count=3, rate=0.3, position=2)
    board = replace(
        board, profile=replace(board.profile, sections=(*board.profile.sections, rare))
    )
    assert [h for h, _ in board.profile.skeleton()] == ["Summary"]
    assert invented_sections("## Workaround\ndo this", board) == ()


def test_a_prose_board_treats_every_heading_as_invented():
    board = _board_with_sections()
    assert invented_sections("## Anything\nx", board) == ("Anything",)


def test_a_short_draft_is_not_accused_of_the_wrong_language():
    # textstats.language returns None on thin evidence; that is "not wrong",
    # not a second complaint stacked on a draft that is already too short.
    board = _board_with_sections("Summary")
    assert wrong_language("Too short.", board) is False


def test_german_prose_on_an_english_board_is_wrong_language():
    board = _board_with_sections("Summary")
    german = (
        "Wenn der Nutzer auf den Knopf klickt, dann wird die Liste nicht mehr "
        "aktualisiert und die Anzeige bleibt auf dem alten Stand stehen."
    )
    assert wrong_language(german, board) is True


def test_a_single_run_has_no_spread_rather_than_zero_spread():
    spread = Spread.of([0.8])
    assert spread is not None
    assert spread.n == 1
    assert spread.stdev == 0.0
    assert spread.low == spread.high == 0.8


def test_failed_runs_are_counted_but_kept_out_of_the_quality_figures():
    board = _board_with_sections("Summary")
    runs = [
        _run(alignment=0.9),
        _run(alignment=None, error="no model", attempts=0),
    ]
    report = score_board(board, runs)
    assert report.runs == 2
    assert report.failed == 1
    assert report.alignment is not None
    # 0.9, not 0.45: an outage is not a score.
    assert report.alignment.mean == 0.9
    assert report.alignment.n == 1


def test_per_case_spread_needs_more_than_one_run_of_that_case():
    board = _board_with_sections("Summary")
    once = score_board(board, [_run("#1", alignment=0.5)])
    assert once.per_case_stdev is None
    twice = score_board(board, [_run("#1", alignment=0.5), _run("#1", alignment=0.9, repeat=1)])
    assert twice.per_case_stdev is not None
    assert twice.per_case_stdev.n == 1


def test_score_says_when_two_models_were_mixed():
    board = _board_with_sections("Summary")
    other = replace(_run(), model="other-1")
    report = score([board], [_run(), other])
    assert report.model == "fixed-1, other-1"


def test_runs_for_an_unknown_board_are_dropped_not_miscounted():
    board = _board_with_sections("Summary")
    stray = replace(_run(), board="gone")
    report = score([board], [_run(), stray])
    assert report.runs == 1


# -------------------------------------------------------------------- baseline


def _report(
    alignment=0.80, boards=(("acme", 0.80),), model="fixed-1", failed=0, runs=10, checks=5
):
    from ticket_ai_mcp.evals.metrics import BoardReport, Report

    return Report(
        model=model,
        boards=tuple(
            BoardReport(
                board=slug,
                cases=1,
                runs=runs,
                failed=failed,
                alignment=Spread.of([value, value]),
                checks=Spread.of([float(checks), float(checks)]),
                human=Spread.of([0.9, 0.9]),
                coverage=Spread.of([0.5, 0.5]),
                human_coverage=Spread.of([0.9, 0.9]),
                per_case_stdev=None,
                seconds=None,
                revised=0.0,
                invented_rate=0.0,
                invented_headings=(),
                wrong_language_rate=0.0,
            )
            for slug, value in boards
        ),
        runs=runs,
        failed=failed,
        alignment=Spread.of([alignment, alignment]),
        pooled=alignment,
        checks=checks * runs,
    )


def test_a_baseline_survives_a_round_trip(tmp_path):
    path = tmp_path / "baseline.json"
    Baseline.of(_report()).save(path)
    back = Baseline.load(path)
    assert back.alignment == 0.80
    assert back.boards == {"acme": 0.80}


def test_a_small_drop_is_noise_and_passes():
    verdict = compare(_report(alignment=0.77), Baseline.of(_report(alignment=0.80)))
    assert verdict.ok
    assert verdict.complaints == ()


def test_a_drop_past_the_tolerance_fails_and_says_by_how_much():
    verdict = compare(_report(alignment=0.70), Baseline.of(_report(alignment=0.80)))
    assert not verdict.ok
    assert "0.100 below the baseline" in verdict.complaints[0]


def test_a_board_that_collapsed_fails_even_when_the_total_holds():
    # The whole point of checking boards as well: three points gained on one
    # and eight lost on another averages out, and the average is the number
    # nobody investigates.
    base = Baseline.of(_report(alignment=0.80, boards=(("acme", 0.80), ("zeta", 0.80))))
    now = _report(alignment=0.80, boards=(("acme", 0.88), ("zeta", 0.70)))
    verdict = compare(now, base)
    assert not verdict.ok
    assert any("board zeta" in c for c in verdict.complaints)


def test_a_vanished_board_is_a_complaint_not_a_silent_improvement():
    base = Baseline.of(_report(boards=(("acme", 0.80), ("zeta", 0.60))))
    verdict = compare(_report(alignment=0.80, boards=(("acme", 0.80),)), base)
    assert not verdict.ok
    assert any("zeta is in the baseline but produced no runs" in c for c in verdict.complaints)


def test_a_different_model_is_a_note_not_a_failure():
    base = Baseline.of(_report(alignment=0.80))
    verdict = compare(_report(alignment=0.60, model="other-1"), base)
    assert any("this run is other-1" in n for n in verdict.notes)
    # Still judged on the numbers: the note explains, it does not excuse.
    assert not verdict.ok


def test_a_new_board_is_a_note_not_a_complaint():
    base = Baseline.of(_report(boards=(("acme", 0.80),)))
    verdict = compare(_report(boards=(("acme", 0.80), ("new", 0.80))), base)
    assert verdict.ok
    assert any("board new is new" in n for n in verdict.notes)


def test_a_run_where_nothing_succeeded_cannot_pass():
    from ticket_ai_mcp.evals.metrics import Report

    empty = Report(model="fixed-1", boards=(), runs=3, failed=3, alignment=None)
    verdict = compare(empty, Baseline.of(_report()))
    assert not verdict.ok
    assert verdict.complaints == ("no run succeeded",)


def test_a_report_with_no_successful_runs_cannot_become_a_baseline():
    from ticket_ai_mcp.evals.metrics import Report

    with pytest.raises(ValueError, match="cannot be a baseline"):
        Baseline.of(Report(model="x", boards=(), runs=1, failed=1, alignment=None))


# ---------------------------------------------------------------------- render


def test_the_report_prints_the_spread_next_to_the_mean():
    text = render(_report(alignment=0.80))
    assert "0.800" in text
    assert "±" in text


def test_one_observation_says_so_instead_of_claiming_zero_spread():
    from ticket_ai_mcp.evals.metrics import Report

    single = Report(model="m", boards=(), runs=1, failed=0, alignment=Spread.of([0.8]), pooled=0.8, checks=5)
    assert "n=1" in render(single)
    assert "±0.000" not in render(single)


def test_the_total_comes_after_the_boards():
    text = render(_report(boards=(("acme", 0.8), ("zeta", 0.8))))
    assert text.index("zeta") < text.index("total")


def test_a_failing_verdict_is_printed_with_its_reasons():
    base = Baseline.of(_report(alignment=0.90))
    verdict = compare(_report(alignment=0.60), base)
    text = render(_report(alignment=0.60), verdict)
    assert "FAIL" in text
    assert "below the baseline" in text


def test_a_run_with_nothing_in_it_says_so():
    from ticket_ai_mcp.evals.metrics import Report

    assert "no runs" in render(Report(model="m", boards=(), runs=0, failed=0, alignment=None))


def test_markdown_is_a_table_with_a_bold_total():
    md = render_markdown(_report())
    assert md.splitlines()[2].startswith("| board |")
    assert "| **total (pooled)** |" in md


def test_markdown_carries_the_verdict():
    base = Baseline.of(_report(alignment=0.90))
    md = render_markdown(_report(alignment=0.60), compare(_report(alignment=0.60), base))
    assert "**FAIL**" in md


# ----------------------------------------------------------------------- judge


def test_a_verdict_is_pulled_out_of_surrounding_prose():
    answer = 'Sure!\n```json\n{"verdict": "partly", "why": "mentions it once"}\n```\nHope that helps'
    assert parse(answer) == ("partly", "mentions it once")


def test_a_verdict_outside_the_three_words_is_an_error_not_a_guess():
    # Rounding "mostly on topic" to "on_topic" would put an invention into the
    # agreement figure that the calibration exists to measure.
    with pytest.raises(ValueError, match="is not one of"):
        parse('{"verdict": "mostly on topic"}')


def test_an_answer_with_no_json_says_what_came_back():
    with pytest.raises(ValueError, match="no JSON object"):
        parse("I think it is fine, honestly")


def test_judging_skips_failed_runs_rather_than_calling_them_off_topic():
    board = _board(cases=(_case("#1"),))
    runs = [replace(_run("#1"), body=""), _run("#1", body="It stops after ten positions.")]
    writer = FixedWriter('{"verdict": "on_topic", "why": "yes"}')
    out = list(judge_runs(runs, [board], writer))
    assert len(out) == 1
    assert writer.calls == 1


def test_a_judge_that_answers_nonsense_becomes_a_record():
    board = _board(cases=(_case("#1"),))
    out = list(judge_runs([_run("#1")], [board], FixedWriter("no idea")))
    assert not out[0].ok
    assert "no JSON object" in out[0].error


# ----------------------------------------------------------------- calibration


def test_a_judge_that_always_says_the_same_thing_scores_zero_not_ninety():
    # Nine on-topic drafts in ten, and a judge that answers on_topic every
    # time: 90% raw agreement, and nothing learned.
    pairs = [("on_topic", "on_topic")] * 9 + [("off_topic", "on_topic")]
    assert cohens_kappa(pairs) == 0.0


def test_perfect_agreement_over_two_categories_is_one():
    pairs = [("on_topic", "on_topic")] * 5 + [("off_topic", "off_topic")] * 5
    assert cohens_kappa(pairs) == 1.0


def test_kappa_is_undefined_when_everyone_used_one_category():
    # Expected agreement is 1.0, so every answer would be perfect agreement.
    # That is a fact about the sample, not about the judge.
    assert cohens_kappa([("on_topic", "on_topic")] * 20) is None
    assert cohens_kappa([]) is None


def test_disagreement_worse_than_chance_goes_negative():
    pairs = [("on_topic", "off_topic")] * 5 + [("off_topic", "on_topic")] * 5
    kappa = cohens_kappa(pairs)
    assert kappa is not None and kappa < 0


def test_agreement_reports_what_only_one_side_saw():
    labels = [Label("acme", "#1", 0, "on_topic"), Label("acme", "#2", 0, "partly")]
    verdicts = [
        JudgeVerdict("acme", "#1", 0, "on_topic"),
        JudgeVerdict("acme", "#3", 0, "partly"),
    ]
    result = agreement(labels, verdicts)
    assert result.pairs == 1
    assert result.labelled_only == 1
    assert result.judged_only == 1


def test_a_broken_judge_record_is_not_compared():
    labels = [Label("acme", "#1", 0, "on_topic")]
    verdicts = [JudgeVerdict("acme", "#1", 0, "", error="no JSON object")]
    assert agreement(labels, verdicts).pairs == 0


def test_the_kappa_band_is_named_and_a_thin_sample_says_so():
    labels = [Label("acme", f"#{i}", 0, "on_topic" if i % 2 else "off_topic") for i in range(10)]
    verdicts = [JudgeVerdict(label.board, label.case, 0, label.verdict) for label in labels]
    result = agreement(labels, verdicts)
    assert result.verdict == "almost perfect"
    assert "too few to quote" in render_agreement(result)


def test_labels_are_read_from_a_file_with_the_line_number_on_errors(tmp_path):
    path = tmp_path / "labels.jsonl"
    path.write_text(
        '{"board": "acme", "case": "#1", "repeat": 0, "verdict": "on_topic"}\n{"board"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"labels\.jsonl:2"):
        load_labels(path)


# ----------------------------------------------------- the U+2028 line splitter


LINE_SEPARATOR = chr(0x2028)


def test_a_record_containing_u2028_is_one_record_not_two(tmp_path):
    # Found on the kern-ux board: a real German ticket carries a literal
    # U+2028. str.splitlines() breaks on it, json.dumps does not escape it, so
    # the file was valid JSONL and the reader cut one record in half and
    # blamed the file.
    text = json.dumps(
        {**_case().to_dict(), "reference": f"erste Zeile{LINE_SEPARATOR}zweite Zeile"},
        ensure_ascii=False,
    )
    assert len(text.splitlines()) == 2
    assert len([line for line in jsonl_lines(text) if line.strip()]) == 1

    directory = _write_board(tmp_path, cases_text=text + "\n")
    board = load_board(directory)
    assert LINE_SEPARATOR in board.cases[0].reference


def test_results_and_labels_survive_u2028_too(tmp_path):
    runs = tmp_path / "runs.jsonl"
    write_runs(runs, [replace(_run(), body=f"vorher{LINE_SEPARATOR}nachher")])
    assert len(read_runs(runs)) == 1

    labels = tmp_path / "labels.jsonl"
    labels.write_text(
        json.dumps({"board": f"a{LINE_SEPARATOR}b", "case": "#1", "verdict": "on_topic"}) + "\n",
        encoding="utf-8",
    )
    assert len(load_labels(labels)) == 1


# ------------------------------------------------- the command CI actually runs


def _tiny_suite(tmp_path, *, alignment=0.80):
    """A dataset and a results file for it, with no model anywhere near."""
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    _write_board(dataset)
    runs = tmp_path / "runs.jsonl"
    write_runs(runs, [replace(_run(), alignment=alignment)])
    return dataset, runs


def test_eval_scores_a_results_file_without_a_model(tmp_path, capsys):
    dataset, runs = _tiny_suite(tmp_path)
    code = main(["eval", "--dataset", str(dataset), "--runs", str(runs)])
    out = capsys.readouterr().out
    assert code == 0
    assert "acme" in out
    assert "total" in out


def test_eval_writes_a_baseline_and_then_holds_itself_to_it(tmp_path, capsys):
    dataset, runs = _tiny_suite(tmp_path, alignment=0.90)
    baseline = tmp_path / "baseline.json"

    assert main(["eval", "--dataset", str(dataset), "--runs", str(runs),
                 "--baseline", str(baseline), "--update-baseline"]) == 0
    capsys.readouterr()

    # Same numbers, so the gate passes.
    assert main(["eval", "--dataset", str(dataset), "--runs", str(runs),
                 "--baseline", str(baseline)]) == 0
    assert "PASS" in capsys.readouterr().out

    # A real drop, so it does not. This is the exit code CI fails on.
    worse = tmp_path / "worse.jsonl"
    write_runs(worse, [replace(_run(), alignment=0.50)])
    assert main(["eval", "--dataset", str(dataset), "--runs", str(worse),
                 "--baseline", str(baseline)]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_eval_markdown_is_a_table(tmp_path, capsys):
    dataset, runs = _tiny_suite(tmp_path)
    main(["eval", "--dataset", str(dataset), "--runs", str(runs), "--markdown"])
    assert "| board |" in capsys.readouterr().out


def test_eval_without_a_model_says_so_rather_than_crashing(tmp_path, capsys, monkeypatch):
    for var in ("TICKET_AI_WRITER", "TICKET_AI_MODEL", "TICKET_AI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    dataset, _ = _tiny_suite(tmp_path)
    assert main(["eval", "--dataset", str(dataset)]) == 2
    assert "No model configured" in capsys.readouterr().err


def test_eval_judge_without_labels_prints_uncalibrated_every_time(tmp_path, capsys, monkeypatch):
    dataset, runs = _tiny_suite(tmp_path)
    monkeypatch.setattr(
        "ticket_ai_mcp.cli.writer_for",
        lambda *a, **k: FixedWriter('{"verdict": "on_topic", "why": "yes"}'),
    )
    assert main(["eval", "--dataset", str(dataset), "--runs", str(runs), "--judge"]) == 0
    out = capsys.readouterr().out
    assert "1/1 on topic" in out
    # The caveat travels with the number rather than being documented once.
    assert "uncalibrated" in out


def test_eval_judge_with_labels_prints_the_agreement_instead(tmp_path, capsys, monkeypatch):
    dataset, runs = _tiny_suite(tmp_path)
    labels = tmp_path / "labels.jsonl"
    labels.write_text(
        json.dumps({"board": "acme", "case": "#7", "repeat": 0, "verdict": "on_topic"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "ticket_ai_mcp.cli.writer_for",
        lambda *a, **k: FixedWriter('{"verdict": "on_topic", "why": "yes"}'),
    )
    main(["eval", "--dataset", str(dataset), "--runs", str(runs),
          "--judge", "--labels", str(labels)])
    out = capsys.readouterr().out
    assert "uncalibrated" not in out
    assert "kappa" in out
    assert "too few to quote" in out


def test_the_report_shows_spread_timing_language_and_findings():
    # The optional lines: they only appear when there is something to say, and
    # a report that stayed silent about them would look clean by omission.
    board = _board_with_sections("Summary")
    runs = [
        _run("#1", alignment=0.6, seconds=40.0, attempts=2, body="## Impact\nWenn der Nutzer "
             "auf den Knopf klickt, dann bleibt die Anzeige auf dem alten Stand stehen und "
             "wird nicht mehr aktualisiert.", findings=("no_labels",)),
        replace(_run("#1", alignment=0.9, seconds=70.0), repeat=1),
    ]
    text = render(score([board], runs))
    assert "same case" in text
    assert "seconds" in text
    assert "invented" in text and "Impact" in text
    assert "wrong one" in text
    assert "no_labels" in text


def test_the_html_page_is_self_contained():
    html = render_html(_report())
    assert html.startswith("<!doctype html>")
    # Nothing to fetch: the bucket serves this file and nothing else.
    for tag in ("<script", "<link", "src=", "@import"):
        assert tag not in html


def test_the_html_page_escapes_what_came_from_a_board():
    from ticket_ai_mcp.evals.metrics import Report

    nasty = Report(
        model='<script>alert("x")</script>',
        boards=(),
        runs=1,
        failed=0,
        alignment=Spread.of([0.5]),
        pooled=0.5,
        checks=5,
    )
    html = render_html(nasty)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_the_html_page_marks_a_failure_and_names_the_reason():
    base = Baseline.of(_report(alignment=0.90))
    html = render_html(_report(alignment=0.50), compare(_report(alignment=0.50), base))
    assert 'class="fail"' in html
    assert "below the baseline" in html


def test_eval_writes_the_page_where_it_was_asked_to(tmp_path, capsys):
    dataset, runs = _tiny_suite(tmp_path)
    page = tmp_path / "out" / "index.html"
    assert main(["eval", "--dataset", str(dataset), "--runs", str(runs), "--html", str(page)]) == 0
    assert page.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_a_missing_default_dataset_explains_that_it_is_not_in_the_wheel(monkeypatch, tmp_path):
    # The likeliest way to see this is `ticket-ai eval` from an installed
    # package. "does not exist" about a path inside site-packages sends people
    # hunting for a broken install.
    monkeypatch.setattr(
        "ticket_ai_mcp.evals.dataset.DATASET_DIR", tmp_path / "not-here"
    )
    with pytest.raises(DatasetError, match="not part of the installed package"):
        load_suite()


def test_an_explicit_missing_directory_stays_a_short_message(tmp_path):
    with pytest.raises(DatasetError, match=r"does not exist$"):
        load_suite(tmp_path / "nope")


# ------------------------------- pooling, because boards do not check equally


def test_the_total_pools_by_checks_instead_of_averaging_over_runs():
    # A board that runs two checks a draft should not weigh as much as one that
    # runs nine. A stub writing English prose scores 1.000 on the prose boards
    # for free, and a plain mean lets that carry the suite.
    strict = replace(_board_with_sections("Summary"), slug="strict")
    loose = replace(_board_with_sections("Summary"), slug="loose")
    runs = [
        CaseRun("strict", "#1", "m", 0, 1.0, attempts=1, alignment=0.40, checks_run=10),
        CaseRun("loose", "#1", "m", 0, 1.0, attempts=1, alignment=1.00, checks_run=2),
    ]
    report = score([strict, loose], runs)

    assert report.alignment is not None
    assert report.alignment.mean == 0.70  # the plain mean over two runs
    # (0.40*10 + 1.00*2) / 12
    assert report.pooled == 0.5
    assert report.checks == 12


def test_the_gate_judges_the_pooled_figure():
    base = Baseline.of(_report(alignment=0.80))
    assert base.alignment == 0.80

    from ticket_ai_mcp.evals.metrics import Report

    # Unweighted mean unchanged, pooled well below: the gate has to see it.
    slipped = Report(
        model="fixed-1",
        boards=_report().boards,
        runs=10,
        failed=0,
        alignment=Spread.of([0.80, 0.80]),
        pooled=0.60,
        checks=50,
    )
    verdict = compare(slipped, base)
    assert not verdict.ok
    assert "pooled alignment 0.600" in verdict.complaints[0]


def test_the_report_prints_checks_next_to_alignment():
    # 1.000 over two checks and 1.000 over nine are not the same claim.
    text = render(_report(checks=2))
    assert "checks" in text
    assert "per draft" in text


def test_a_report_with_no_checks_at_all_has_no_pooled_figure():
    board = _board_with_sections("Summary")
    runs = [CaseRun("acme", "#1", "m", 0, 1.0, attempts=1, alignment=1.0, checks_run=0)]
    report = score([board], runs)
    # Not 0.0 and not 1.0: nothing was checked, which is not the same as
    # everything passing.
    assert report.pooled is None
    assert report.checks == 0


def test_the_ceiling_is_the_board_own_tickets_not_one_point_oh(tmp_path):
    # A reference that satisfies the profile scores well; one that does not
    # scores badly. Either way the figure comes from real text, not from an
    # assumption that a perfect draft exists.
    board = _board_with_sections("Summary")
    good = replace(_case("#1"), reference="## Summary\n" + ("It stops after ten. " * 40))
    poor = replace(_case("#2"), reference="broken")
    board = replace(board, cases=(good, poor))

    spread = ceiling(board)
    assert spread is not None
    assert spread.n == 2
    assert spread.low < spread.high


def test_the_ceiling_reaches_the_report_and_the_render():
    board = _board_with_sections("Summary")
    report = score([board], [_run()])
    assert report.boards[0].human is not None
    assert "human" in render(report)
    assert "own tickets" in render(report)


# ------------------------------------------- coverage, which omission cannot game


def test_coverage_cannot_be_raised_by_writing_less():
    # The whole point. alignment goes up when a draft writes nothing, because
    # fewer checks apply. Coverage's denominator is the skeleton, so it does
    # not move.
    board = _board_with_sections("Summary", "Steps")
    assert skeleton_coverage("nothing at all, just prose", board) == 0.0
    assert skeleton_coverage("## Summary\ntext", board) == 0.5
    assert skeleton_coverage("## Summary\ntext\n## Steps\nmore", board) == 1.0


def test_coverage_is_undefined_on_a_board_with_no_skeleton():
    # Not 0.0: a prose board asked for no headings, and scoring a draft zero
    # for obeying that would punish it for being right.
    assert skeleton_coverage("## Anything\nx", _board_with_sections()) is None


def test_coverage_counts_the_skeleton_not_the_whole_vocabulary():
    # The opposite choice from invented_sections, on purpose. Invention needs
    # the full vocabulary; coverage needs what the prompt actually asked for.
    from ticket_ai_mcp.profile import Section

    board = _board_with_sections("Summary")
    rare = Section(heading="Workaround", key="workaround", count=3, rate=0.3, position=2)
    board = replace(
        board, profile=replace(board.profile, sections=(*board.profile.sections, rare))
    )
    assert [h for h, _ in board.profile.skeleton()] == ["Summary"]
    assert skeleton_coverage("## Workaround\nx", board) == 0.0
    assert invented_sections("## Workaround\nx", board) == ()


def test_the_report_shows_coverage_against_the_board_own_tickets():
    board = _board_with_sections("Summary")
    good = replace(_case("#1"), reference="## Summary\n" + ("text " * 50))
    board = replace(board, cases=(good,))
    report = score([board], [_run("#1", body="no headings here at all")])

    assert report.boards[0].coverage is not None
    assert report.boards[0].coverage.mean == 0.0
    assert report.boards[0].human_coverage is not None
    assert report.boards[0].human_coverage.mean == 1.0
    assert "of the skeleton" in render(report)


def test_the_gate_catches_a_coverage_drop_that_alignment_hides():
    # The scenario coverage was added for: drafts quietly stop writing the
    # skeleton. Fewer checks apply, so alignment holds or rises, and the one
    # figure that moved is the one that cannot be gamed.
    before = _report(alignment=0.80)
    base = Baseline.of(before)
    assert base.coverage == {"acme": 0.5}

    from ticket_ai_mcp.evals.metrics import BoardReport, Report

    slipped_board = replace(before.boards[0], coverage=Spread.of([0.1, 0.1]))
    assert isinstance(slipped_board, BoardReport)
    slipped = Report(
        model="fixed-1",
        boards=(slipped_board,),
        runs=10,
        failed=0,
        alignment=Spread.of([0.80, 0.80]),
        pooled=0.80,
        checks=50,
    )
    verdict = compare(slipped, base)
    assert not verdict.ok
    assert "covers 0.100 of its skeleton" in verdict.complaints[0]


def test_a_board_that_lost_its_skeleton_is_its_own_complaint():
    base = Baseline.of(_report(alignment=0.80))
    gone = replace(_report(alignment=0.80).boards[0], coverage=None)
    from ticket_ai_mcp.evals.metrics import Report

    now = Report(
        model="fixed-1",
        boards=(gone,),
        runs=10,
        failed=0,
        alignment=Spread.of([0.80, 0.80]),
        pooled=0.80,
        checks=50,
    )
    verdict = compare(now, base)
    assert not verdict.ok
    assert any("no longer has a skeleton" in c for c in verdict.complaints)


def test_a_baseline_round_trips_its_coverage(tmp_path):
    path = tmp_path / "baseline.json"
    Baseline.of(_report()).save(path)
    assert Baseline.load(path).coverage == {"acme": 0.5}


def test_a_prose_board_contributes_no_coverage_baseline():
    # No skeleton, no coverage, nothing to gate. Storing 0.0 would make every
    # later run look like it had collapsed.
    board = _board_with_sections()
    report = score([board], [_run()])
    assert report.boards[0].coverage is None
    assert Baseline.of(report).coverage == {}


def test_markdown_prints_every_figure_against_the_board_own_tickets():
    # A bare column of scores invites reading them against 1.0, and nothing in
    # this dataset reaches 1.0 - not even the people whose board it is.
    md = render_markdown(_report())
    assert "alignment / theirs" in md
    assert "coverage / theirs" in md
    assert "0.800 / 0.900" in md


def test_a_prose_board_shows_a_dash_for_coverage_not_a_zero():
    board = _board_with_sections()
    md = render_markdown(score([board], [_run()]))
    assert "| - |" in md


def test_the_html_page_carries_the_same_columns():
    html = render_html(_report())
    assert "alignment / theirs" in html
    assert "coverage / theirs" in html
    assert "total (pooled)" in html
