# SPDX-License-Identifier: MIT

"""Measuring the one part of this tool that a model writes.

Everything else here counts, and counting is tested the ordinary way. Composing
a ticket is not: the same input gives a different answer twice in a row, so a
unit test can only check that the plumbing holds - and every test in `tests/`
does exactly that, with the writer mocked. Nothing in this repository has ever
measured how good the real output is.

`docs/local-models.md` came closest: seven titles, one model, one pass. That is
a demo. The difference between it and an evaluation is a dataset that does not
move, a number repeated often enough to have a spread, and a baseline that
makes a regression visible without anyone remembering last month's figure.

The dataset lives in `evals/dataset/` as files, not as a script that fetches
boards when it runs. An evaluation whose input changes underneath it is not
measuring the thing it appears to measure.
"""

from .dataset import Board, Case, load_board, load_suite
from .runner import CaseRun, run_case, run_suite

__all__ = [
    "Board",
    "Case",
    "CaseRun",
    "load_board",
    "load_suite",
    "run_case",
    "run_suite",
]
