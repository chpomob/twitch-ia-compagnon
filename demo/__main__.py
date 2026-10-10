"""``python -m demo``: module entry point, beside ``demo/__init__.py``."""

from __future__ import annotations

from . import main

if __name__ == "__main__":
    raise SystemExit(main())
