"""Global pytest safety boundary.

Some API test modules import :mod:`pharos.main` during collection, before a
fixture can override application settings.  Point that earliest import at an
isolated temporary data directory so an ordinary ``pytest`` invocation can
never migrate or write the developer's real ``data/pharos.db``.
"""

from __future__ import annotations

import os
import tempfile

_TEST_DATA_DIR = tempfile.TemporaryDirectory(prefix="pharos-pytest-")
os.environ["PHAROS_DATA_DIR"] = _TEST_DATA_DIR.name
