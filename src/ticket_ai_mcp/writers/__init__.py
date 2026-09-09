# SPDX-License-Identifier: MIT

"""Model backends. None of them is required.

Importing a backend registers it, so they are imported here rather than
lazily - `available()` should tell the truth before anyone asks for one by
name.

One backend, two names, because the OpenAI chat protocol is what nearly every
provider and every local runner speaks. Adding a vendor-specific backend should
need a reason the shared protocol cannot meet.
"""

from __future__ import annotations

from . import openai_compatible  # noqa: F401  (imported for the side effect)
from .base import Writer, WriterError, available, build, register

__all__ = ["Writer", "WriterError", "available", "build", "register"]
