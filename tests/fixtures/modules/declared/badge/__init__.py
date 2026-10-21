"""Declared trigger type fixture — a minimal input that opens nothing (phase 8, R1).

The manifest beside this package carries the AC1 ``badge_tier`` declaration.
``activate`` returns a handle whose ``start_inputs`` opens no source: the
fixture exists for its manifest, which the loader parses at discovery.
"""

from __future__ import annotations

from typing import Any


class Handle:
    async def start_inputs(self) -> None:
        return None

    async def close(self) -> None:
        return None


async def activate(context: Any, settings: Any, catalog: Any) -> Handle:
    return Handle()
