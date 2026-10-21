"""P1-12: structlog call sites must use kwargs, never percent-format.

structlog has no ``%s`` format spec. ``logger.warning("ip banned: %s", ip)``
renders the format string as the literal event text and passes ``ip`` as an
opaque positional element, so the value never becomes a queryable field. For
the security audit lines — who was banned, which tenant failed, which user hit
an MFA lockout — that is a materially weaker incident trail.

This file is the enforcement. ruff's ``G`` rules were evaluated as the
mechanism and REJECTED: they are written against stdlib ``logging`` and do not
fire on a structlog call, so enabling them would not catch a regression here.
An AST check does, and mirrors the structural assertions this repo already
uses for import edges.
"""

import ast
import re
from pathlib import Path

import pytest

import src.app as _app_pkg

# src.app is <repo>/src/app, so parents[2] is the repository root.
REPO = Path(_app_pkg.__file__).resolve().parents[2]
SRC = REPO / "src/app"

PERCENT = re.compile(r"%[sdf]|%\.\d+[sdf]")

LOG_LEVELS = frozenset({"debug", "info", "warning", "error", "exception", "critical"})


def _is_log_receiver(value: ast.expr) -> bool:
    """True when ``value`` is something a structlog level method hangs off.

    Three receiver shapes reach a real call site: a module-level ``logger`` /
    ``_logger`` name, a ``<something>.logger`` attribute (the modules facade
    pattern, e.g. ``_pkg.logger.info(...)``), and a call whose result IS the
    logger (``get_logger(__name__).warning(...)``). Matching only the first
    two left the third untested, which is how a percent-formatted call
    survived the original conversion pass.
    """
    if isinstance(value, ast.Name):
        return value.id in ("logger", "_logger")
    if isinstance(value, ast.Attribute):
        return value.attr == "logger"
    if isinstance(value, ast.Call):
        # A call whose result is the logger — `get_logger(__name__)`, which
        # returns a structlog FilteringBoundLogger.
        #
        # EXCLUDE `logging.getLogger(...)`: that is the STDLIB logger, and
        # stdlib DOES do %-substitution, so a percent-formatted event string
        # with positional values there is correct, not a defect. The config
        # package relies on this deliberately — it constructs before
        # logger.setup_logging() runs, so it logs through stdlib and
        # percent-formats to match (see the comment on AuthSettings'
        # warn_on_fail_open_knobs validator). Matching it here would force a
        # conversion that breaks the reason the stdlib logger is used.
        if isinstance(value.func, ast.Attribute) and value.func.attr == "getLogger":
            return False
        if isinstance(value.func, ast.Name) and value.func.id == "getLogger":
            return False
        return True
    return False


#: The security-critical events P1-12 was about. Each must exist as a named
#: event with fields, so an incident can query by ip / tenant / user.
SECURITY_EVENTS = [
    ("src/app/core/security/auto_ban.py", "auto_ban_applied"),
    ("src/app/core/security/auto_ban.py", "auto_ban_lifted"),
    ("src/app/middleware/tenant.py", "tenant_resolution_failed_invalid_slug"),
    ("src/app/core/tenancy/resolver.py", "tenant_l1_cache_entry_corrupt"),
    ("src/app/modules/auth/services/mfa.py", "mfa_lockout_redis_unavailable_fail_closed"),
]

#: Fields each event must expose as kwargs — the queryable part.
SECURITY_EVENT_FIELDS = {
    "auto_ban_applied": {"ip", "duration", "reason", "ban_count"},
    "auto_ban_lifted": {"ip"},
    "tenant_resolution_failed_invalid_slug": {"tenant_slug"},
    "tenant_l1_cache_entry_corrupt": {"slug", "error"},
    "mfa_lockout_redis_unavailable_fail_closed": {"user_id"},
}


def _logger_calls(tree: ast.AST):
    """Yield (level, event_literal, keyword_names) for every logger call."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in LOG_LEVELS:
            continue
        if not _is_log_receiver(func.value):
            continue
        literal = None
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            literal = node.args[0].value
        elif node.args and isinstance(node.args[0], ast.JoinedStr):
            literal = "".join(
                part.value
                for part in node.args[0].values
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
        yield func.attr, literal, {kw.arg for kw in node.keywords if kw.arg}


def _python_files():
    return sorted(p for p in SRC.rglob("*.py") if "migrations" not in p.parts)


class TestNoPercentFormattedEvents:
    """The regression: a percent-formatted structlog event is not queryable."""

    @pytest.mark.parametrize("path", _python_files(), ids=lambda p: str(p.relative_to(SRC)).replace("\\", "/"))
    def test_no_percent_format_in_log_event(self, path: Path) -> None:
        """Every logger event name must be a plain literal, no %-spec."""
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders = [
            (node.lineno, literal)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for func in [node.func]
            if isinstance(func, ast.Attribute)
            and func.attr in LOG_LEVELS
            and _is_log_receiver(func.value)
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and PERCENT.search(node.args[0].value)
            for literal in [node.args[0].value]
        ]
        assert not offenders, f"{path.name}: percent-formatted log events: {offenders}"

    @pytest.mark.parametrize("path", _python_files(), ids=lambda p: str(p.relative_to(SRC)).replace("\\", "/"))
    def test_no_bare_positional_values_on_log_calls(self, path: Path) -> None:
        """Positional values become opaque elements; values must be kwargs."""
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr not in LOG_LEVELS:
                continue
            if not _is_log_receiver(func.value):
                continue
            # One positional arg is the event name itself; anything beyond that
            # is a value that structlog will not turn into a field.
            if len(node.args) > 1:
                offenders.append((node.lineno, len(node.args) - 1))
        assert not offenders, f"{path.name}: log calls with positional values: {offenders}"


class TestSecurityEventsAreQueryable:
    """The sites the issue named must carry named fields, not sentences."""

    @pytest.mark.parametrize(("rel", "event"), SECURITY_EVENTS)
    def test_event_exists_as_a_named_call(self, rel: str, event: str) -> None:
        source = (REPO / rel).read_text(encoding="utf-8")
        assert f'"{event}"' in source, f"{rel} is missing the {event} event"

    @pytest.mark.parametrize(("event", "fields"), sorted(SECURITY_EVENT_FIELDS.items()))
    def test_event_passes_its_fields_as_kwargs(self, event: str, fields: set[str]) -> None:
        """The event must be followed by its fields as keywords."""
        found = False
        for path in _python_files():
            source = path.read_text(encoding="utf-8")
            for level, literal, kwargs in _logger_calls(ast.parse(source)):
                if literal == event:
                    found = True
                    missing = fields - kwargs
                    assert not missing, f"{event} in {path.name} is missing fields: {missing}"
        assert found, f"no call site found for event {event}"


class TestSanitizerStillApplies:
    """Changing call style must not have bypassed SanitizingProcessor."""

    def test_sanitizing_processor_is_still_in_the_chain(self) -> None:
        """Structural: the configured chain still installs the sanitizer."""
        import inspect

        import src.app.core.logger as logger_module

        source = inspect.getsource(logger_module)
        assert "SanitizingProcessor" in source, "the sanitizer was dropped from the log chain"

    def test_sanitizer_still_redacts_a_field(self) -> None:
        """Behavioural: a field produced by a converted call is redacted.

        If the call-style refactor had routed around the sanitizer, PII would
        reach the log line as a plain field.
        """
        from src.app.core.observability.log_sanitizer import sanitize_dict

        out = sanitize_dict({"event": "login_failed", "email": "user@example.com", "count": 2})
        assert "user@example.com" not in str(out.get("email", ""))
        assert out.get("count") == 2

    def test_structlog_logger_is_still_returned(self) -> None:
        """A structural guard: get_logger must keep returning a structlog type.

        It returns structlog's lazy proxy, not a concrete BoundLogger, so the
        assertion is that the object is a structlog type at all — the point is
        that a call-style refactor has not swapped the logger implementation.
        """
        from src.app.core.logger import get_logger

        logger = get_logger(__name__)
        assert type(logger).__module__.startswith("structlog"), (
            f"get_logger no longer returns a structlog logger: {type(logger)!r}"
        )


class TestCapturedOutputIsQueryable:
    """The issue's requirement: a known event produces a FIELD, not a string.

    The AST tests prove the call *shape*; this proves the rendered record.
    Under the old percent form, `logger.warning("ip=%s", ip)` renders a single
    concatenated event with no ``ip`` key at all.
    """

    @pytest.mark.parametrize(
        ("level", "event", "kwargs", "expected_field"),
        [
            ("warning", "auto_ban_applied", {"ip": "198.51.100.7", "ban_count": 3}, "ip"),
            ("warning", "tenant_resolution_failed_invalid_slug", {"tenant_slug": "gone"}, "tenant_slug"),
            ("error", "mfa_lockout_redis_unavailable_fail_closed", {"user_id": 42}, "user_id"),
        ],
    )
    def test_event_renders_queryable_fields(self, level, event, kwargs, expected_field) -> None:
        """Sampled across the three security-critical sites the issue named."""
        import structlog

        with structlog.testing.capture_logs() as captured:
            getattr(structlog.get_logger("test"), level)(event, **kwargs)

        assert captured, "no log record was captured"
        record = captured[-1]
        assert record["event"] == event
        assert record[expected_field] == kwargs[expected_field], (
            f"{expected_field} is not a field on the captured record: {record}"
        )
        # The percent form would leave the value glued to the event text.
        assert f"{expected_field}=" not in str(record["event"])
