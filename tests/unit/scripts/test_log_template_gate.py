"""The log-template gate must catch a dangling placeholder, and only that.

``scripts/checks/log_templates.py`` rejects structlog event messages that
keep a slot where a value was removed: a space before a trailing colon
(``"Failed to upload file :"``), an emptied slot between two labels
(``"Uploaded file: , size: bytes"``), or empty quotes where a value
belongs (``"Preloaded script ''"``).

Two things are pinned here, and the second is the one that matters:

1. **The gate catches the defects.** Each of the three shapes is fed to
   the checker and must be reported.

2. **The gate does NOT flag the label-prefix shape.** A bare trailing
   colon with no space before it (``"Deleted file:"``) is a label
   prefix, not a dangling slot, and the repo uses that shape in about
   fourteen places. A gate that flagged it would have demanded edits to
   a dozen modules that have nothing to do with this defect. The gate
   leaving that shape alone is a deliberate design decision, and this
   test is what keeps it a decision rather than an accident.

The tempting wrong fix is also pinned: replacing the slot with
``{placeholder}``. structlog does not interpolate braces from keyword
arguments, so the braces would render literally. The gate's message
says so, and the test checks that the guidance is there — a future
maintainer who "fixes" a violation by adding braces has been told why
not to.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parents[3]
CHECKER = REPO / "scripts" / "checks" / "log_templates.py"


def _load_checker() -> ModuleType:
    """Import the checker as a module (it is not on sys.path)."""
    spec = importlib.util.spec_from_file_location("_log_templates_gate", CHECKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TestDanglingSlotsAreCaught:
    """Each reported shape must be reported."""

    def test_space_before_trailing_colon(self) -> None:
        checker = _load_checker()
        reason = checker._message_reason("Failed to upload file :")
        assert reason is not None, "a space before a trailing colon is a dangling slot"

    def test_emptied_slot_between_labels(self) -> None:
        checker = _load_checker()
        reason = checker._message_reason("Uploaded file: , size: bytes")
        assert reason is not None, "an emptied slot between two labels is a dangling slot"

    def test_empty_quotes(self) -> None:
        checker = _load_checker()
        reason = checker._message_reason("Preloaded script ''")
        assert reason is not None, "empty quotes where a value belongs is a dangling slot"

    def test_a_real_file_with_a_defect_is_reported(self, tmp_path: Path) -> None:
        checker = _load_checker()
        target = tmp_path / "broken.py"
        target.write_text(
            'from src.app.core.logger import get_logger\n'
            "\n"
            "logger = get_logger(__name__)\n"
            "\n"
            "\n"
            'def go(path: str) -> None:\n'
            '    logger.error("Failed to upload file :", path=path)\n',
            encoding="utf-8",
        )
        offenses = checker.check_file(target)
        assert len(offenses) == 1, f"expected one offense, got {offenses}"
        assert offenses[0].line == 7, f"the offending line must be reported: {offenses[0]}"
        assert "Failed to upload file" in offenses[0].message

    def test_a_clean_file_is_not_reported(self, tmp_path: Path) -> None:
        checker = _load_checker()
        target = tmp_path / "clean.py"
        target.write_text(
            'from src.app.core.logger import get_logger\n'
            "\n"
            "logger = get_logger(__name__)\n"
            "\n"
            "\n"
            "def go(path: str) -> None:\n"
            '    logger.error("Failed to upload file", path=path)\n',
            encoding="utf-8",
        )
        assert checker.check_file(target) == []


class TestLabelPrefixIsNotFlagged:
    """A bare trailing colon is a label prefix, not a dangling slot."""

    def test_bare_trailing_colon_is_not_a_defect(self) -> None:
        checker = _load_checker()
        assert checker._message_reason("Deleted file:") is None
        assert checker._message_reason("Blob not found for deletion:") is None
        assert checker._message_reason("Session migration completed") is None

    def test_f_string_is_never_flagged(self, tmp_path: Path) -> None:
        """An f-string interpolates at runtime, so it cannot dangle."""
        checker = _load_checker()
        target = tmp_path / "fstring.py"
        target.write_text(
            'from src.app.core.logger import get_logger\n'
            "\n"
            "logger = get_logger(__name__)\n"
            "\n"
            "\n"
            "def go(path: str) -> None:\n"
            '    logger.error(f"Failed to upload {path} :")\n',
            encoding="utf-8",
        )
        assert checker.check_file(target) == []


class TestGuidance:
    """The fix guidance must warn against the brace mistake."""

    def test_guidance_warns_against_brace_interpolation(self, tmp_path: Path, capsys: object) -> None:
        """A violation must print the brace warning.

        The warning is the point of the gate's output: structlog does not
        interpolate braces from keyword arguments, so a maintainer who
        "fixes" a violation by writing ``{path}`` produces a log line
        that renders the brace literally. The gate has to say so, because
        the broken template is more obviously broken than the guidance
        that explains how to fix it.
        """
        checker = _load_checker()
        original_root = checker.ROOT
        original_prod_roots = checker.PROD_ROOTS
        try:
            checker.ROOT = tmp_path
            checker.PROD_ROOTS = ("src",)
            broken = tmp_path / "src" / "broken.py"
            broken.parent.mkdir(parents=True, exist_ok=True)
            broken.write_text(
                'from src.app.core.logger import get_logger\n'
                "\n"
                "logger = get_logger(__name__)\n"
                "\n"
                "\n"
                "def go(path: str) -> None:\n"
                '    logger.error("Failed to upload file :", path=path)\n',
                encoding="utf-8",
            )
            exit_code = checker.main([])
        finally:
            checker.ROOT = original_root
            checker.PROD_ROOTS = original_prod_roots

        out = capsys.readouterr().out  # type: ignore[attr-defined]
        assert exit_code == 1, f"the broken tree must fail:\n{out}"
        assert "not interpolate braces" in out.lower(), f"the brace warning must be printed:\n{out}"
        assert "Failed to upload file" in out, f"the offending template must be named:\n{out}"


class TestTheRealTreePasses:
    def test_repo_tree_has_no_dangling_slots(self) -> None:
        checker = _load_checker()
        assert checker.main([]) == 0, "the real tree has a dangling log-template slot"
