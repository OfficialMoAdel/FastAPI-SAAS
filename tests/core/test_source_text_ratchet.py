"""Ratchet for source-text assertions in the test suite.

The property is documented in ``docs/testing/source-text-ratchet.md``: the
count may fall, never rise. A new assertion on a ``.py`` file's text (or
``inspect.getsource``) without an accompanying behavioural check is the habit
this gate exists to stop.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parents[2]
COUNTER = REPO / "scripts" / "count_source_text_tests.py"

SOURCE_TEXT_TEST_BASELINE = 162


def _load_counter() -> ModuleType:
    spec = importlib.util.spec_from_file_location("count_source_text_tests", COUNTER)
    if spec is None or spec.loader is None:  # pragma: no cover - import machinery
        raise ImportError(f"cannot load {COUNTER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


def test_source_text_test_count_does_not_increase() -> None:
    measured = _load_counter().count()

    assert measured <= SOURCE_TEXT_TEST_BASELINE, (
        f"source-text test count rose to {measured} (baseline "
        f"{SOURCE_TEXT_TEST_BASELINE}). Convert the new assertion to a "
        "behavioural test, or record the increase with a reason in "
        "docs/testing/source-text-ratchet.md and raise the baseline."
    )
