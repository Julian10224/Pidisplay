"""Lokaal digital-signage-systeem voor Raspberry Pi."""

import os

_here = os.path.dirname(os.path.abspath(__file__))


def _read_version():
    for candidate in (os.path.join(_here, "..", "..", "VERSION"), os.path.join(_here, "VERSION")):
        try:
            with open(candidate, "r", encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            continue
    return "0.0.0-dev"


__version__ = _read_version()
