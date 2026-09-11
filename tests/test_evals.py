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

import pytest

from ticket_ai_mcp.evals.dataset import (
    Board,
    Case,
    DatasetError,
    load_board,
    load_suite,
    write_board,
)
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
