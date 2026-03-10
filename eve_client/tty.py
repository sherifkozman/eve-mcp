"""Shared TTY detection helper."""

from __future__ import annotations

import sys


def stdin_is_tty() -> bool:
    """Return True when stdin is attached to a terminal."""
    try:
        return bool(sys.stdin.isatty())
    except Exception:
        return False
