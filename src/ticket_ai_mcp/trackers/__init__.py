# SPDX-License-Identifier: MIT

"""Tracker adapters.

Importing an adapter module is what registers it, so they are imported here
rather than lazily. Three small imports at startup is a fair price for
`available()` telling the truth before anyone has asked for a tracker by name.
"""

from __future__ import annotations

from . import github, gitlab, jira  # noqa: F401  (imported for the side effect)
from .base import Tracker, TrackerError, available, build, register

__all__ = ["Tracker", "TrackerError", "available", "build", "register"]
