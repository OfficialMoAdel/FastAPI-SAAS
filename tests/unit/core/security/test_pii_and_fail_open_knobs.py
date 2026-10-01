"""PII in log sites, and startup warnings for opened fail-open auth knobs.

Two defects, one file because both are the same class of error: a security
posture that the CODE honours but never TELLS the operator about.

**PII.** The sites under test name the subject a decision is about. The log
sanitizer does not cover that, and this module proves it rather than assuming
it: ``LogSanitizer._SENSITIVE_KEY_RE`` matches
password/secret/token/auth/credential and NOT ``user``/``username``/
``email``, so a key-based ``user=`` was never even a candidate. A bare subject
trips no value pattern either (no "@", no digit shape), and an email subject
was only shape-masked to ``a***@corp.example.com`` — which still names the
employer and is trivially re-identified. The assertion that matters is
therefore the NEGATIVE one: after the fix the rendered field must not merely
be differently masked, it must not contain the subject at all.

**Fail-open knobs.** All four auth knobs default to the secure posture, so
the failure mode is silence — the operator sets ``fail_open``, boots cleanly,
and only learns the cost during an outage. The default-quiet half is a real
assertion here and not a formality: a warning that fires on defaults is noise
that trains operators to ignore startup logs, which is worse than no warning.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.core.config.auth import AuthSettings
from src.app.core.observability.log_sanitizer import LogSanitizer
from src.app.core.security.jwt_service import (
    TokenType,
    _load_token_version,
    _resolve_tenant_id_for_token,
    subject_log_ref,
    verify_token,
)

#: A subject in each shape the real code carries. ``sub`` is a username OR an
#: email, and the two are treated differently by the sanitizer — the bug is
#: only fully characterised by testing both.
BARE_SUBJECT = "alice_hunt"
SUBJECT = "alice_hunt@corp.example.com"

#: The four knobs whose fail-closed default can be opened by an operator:
#: (field, opening value, secure default).
FAIL_OPEN_KNOBS = [
    ("AUDIT_LOG_FAILURE_MODE", "fail_open", "fail_closed"),
    ("TOKEN_CACHE_FAILURE_MODE", "fail_open", "fail_closed"),
    ("SESSION_REGISTRATION_FAIL_MODE", "fail_open", "fail_closed"),
    ("MFA_LOCKOUT_FAIL_CLOSED", False, True),
]

WARNING_MARKER = "fail-open knobs are OPEN"


class _WarningRecorder(logging.Handler):
    """Collect rendered warning messages from the config logger."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _build_with_warnings(**overrides: Any) -> tuple[AuthSettings, list[str]]:
    """Construct AuthSettings, returning the fail-open warnings it emitted."""
    logger = logging.getLogger("app.core.config")
    recorder = _WarningRecorder()
    logger.addHandler(recorder)
    previous_level = logger.level
    logger.setLevel(logging.WARNING)
    try:
        settings = AuthSettings(**overrides)
    finally:
        logger.removeHandler(recorder)
        logger.setLevel(previous_level)
    return settings, [m for m in recorder.messages if WARNING_MARKER in m]


class _RecordingLogger:
    """A logger-shaped object that records the event dicts it is called with.

    ``structlog.testing.capture_logs()`` is NOT usable here: it swaps the
    GLOBAL processor chain, and other suites in the tree (notably
    ``test_log_sanitizer.py``) call ``structlog.configure()`` without
    restoring it — so the capture context becomes a no-op depending on run
    order and the record leaks to the real handlers instead. Substituting the
    module's own logger object for the duration of the call intercepts the
    kwargs at the call site and cannot be perturbed by whatever another test
    left configured globally.
    """

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def _record(self, event: Any, kwargs: dict[str, Any]) -> None:
        record = dict(kwargs)
        record["event"] = event
        self.events.append(record)

    def debug(self, event: Any, **kwargs: Any) -> None:
        self._record(event, kwargs)

    def info(self, event: Any, **kwargs: Any) -> None:
        self._record(event, kwargs)

    def warning(self, event: Any, **kwargs: Any) -> None:
        self._record(event, kwargs)

    def error(self, event: Any, **kwargs: Any) -> None:
        self._record(event, kwargs)

    def critical(self, event: Any, **kwargs: Any) -> None:
        self._record(event, kwargs)

    def exception(self, event: Any, **kwargs: Any) -> None:
        self._record(event, kwargs)

    def only(self, event: str) -> dict[str, Any]:
        """The single recorded event named ``event``."""
        matching = [e for e in self.events if e.get("event") == event]
        assert len(matching) == 1, f"expected one {event!r}, got {matching!r} of {self.events!r}"
        return matching[0]


@contextmanager
def _recording_logs(module_path: str, attribute: str) -> Iterator[_RecordingLogger]:
    """Temporarily replace ``module.attribute`` with a recording logger."""
    recorder = _RecordingLogger()
    with patch(f"{module_path}.{attribute}", recorder):
        yield recorder


def _jwt_payload(**overrides: Any) -> dict[str, Any]:
    """A minimal ACCESS payload, as ``decode_verified_token`` would return."""
    payload = {
        "sub": SUBJECT,
        "token_type": "access",
        "jti": "jti-1",
        "token_version": 5,
        "tenant_id": 1,
    }
    payload.update(overrides)
    return payload


def _cached_user_payload() -> dict[str, Any]:
    """A cached row shaped like what ``UserRead.model_validate`` accepts."""
    return {
        "id": 1,
        "username": BARE_SUBJECT,
        "name": "Alice",
        "email": SUBJECT,
        "profile_image_url": "https://cdn.example.com/a.png",
        "tier_id": 1,
        "tenant_id": str(uuid4()),
        "created_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
    }


def _fake_redis(**methods: Any) -> MagicMock:
    """A Redis double whose named methods are the given AsyncMocks."""
    redis = MagicMock()
    for name, impl in methods.items():
        setattr(redis, name, impl)
    return redis


class TestSanitizerDoesNotCoverSubjects:
    """The premise, proven — otherwise the fix below is unmotivated."""

    def test_subject_keys_are_not_sensitive(self) -> None:
        """``user``/``username``/``email`` never match the key regex, so a
        key-based ``user=`` was never even a candidate for redaction."""
        for key in ("user", "username", "email", "user_id", "sub"):
            assert not LogSanitizer._SENSITIVE_KEY_RE.search(key), f"{key} would already be redacted"

    def test_bare_subject_survives_sanitization_verbatim(self) -> None:
        """The leak, measured on the real sanitizer."""
        assert LogSanitizer.sanitize_message(BARE_SUBJECT) == BARE_SUBJECT
        assert LogSanitizer.sanitize_dict({"user": BARE_SUBJECT})["user"] == BARE_SUBJECT

    def test_email_subject_is_only_shape_masked(self) -> None:
        """Partial masking still discloses the employer and is re-identifiable."""
        masked = LogSanitizer.sanitize_dict({"user": SUBJECT})["user"]
        assert masked == "a***@corp.example.com"
        assert "corp.example.com" in masked


class TestSubjectLogRef:
    """The replacement must be non-reversible AND still correlatable."""

    @pytest.mark.parametrize(
        "subject",
        [BARE_SUBJECT, "First.Last", SUBJECT],
        ids=["bare-username", "dotted-username", "email"],
    )
    def test_does_not_contain_the_subject(self, subject: str) -> None:
        ref = subject_log_ref(subject)
        assert subject not in ref
        assert subject.split("@")[0] not in ref, "the local part alone is still identifying"

    def test_is_stable_for_the_same_subject(self) -> None:
        """An incident must still be followable across events."""
        assert subject_log_ref(BARE_SUBJECT) == subject_log_ref(BARE_SUBJECT)

    def test_distinguishes_different_subjects(self) -> None:
        assert subject_log_ref(BARE_SUBJECT) != subject_log_ref("bob_hunt")

    @pytest.mark.parametrize("empty", [None, ""], ids=["none", "empty"])
    def test_absent_subject_stays_greppable(self, empty: str | None) -> None:
        assert subject_log_ref(empty) == "anonymous"

    def test_ref_survives_the_sanitizer_unchanged(self) -> None:
        """A hex ref must not be re-mangled or dropped downstream."""
        ref = subject_log_ref(SUBJECT)
        assert LogSanitizer.sanitize_message(ref) == ref

    def test_ref_is_not_correlatable_with_a_token_digest(self) -> None:
        """A distinct domain string, so a log ref and a stored token digest
        for the same input can never be joined."""
        from src.app.core.security import tokens

        subject = "some-credential"
        log_ref = subject_log_ref(subject)
        assert log_ref not in tokens.token_digest_candidates(subject)
        assert log_ref not in tokens.legacy_token_digest(subject)


class TestJwtLogSitesCarryNoPII:
    """Driven at the real call sites — no source-text reading."""

    LOGGER = "src.app.core.security.jwt_service._jwt_logger"

    def test_token_version_lookup_unwired_does_not_leak(self) -> None:
        """A fail-CLOSED raise is the most-read event in an incident, and it
        must not carry the subject it is about."""
        from src.app.shared.exceptions import ServiceUnavailableError

        with (
            patch("src.app.core.security.jwt_service._token_version_lookup", None),
            _recording_logs(*self.LOGGER.split(".", 1)) as logs,
        ):
            with pytest.raises(ServiceUnavailableError):
                asyncio.run(_load_token_version("cache-key", SUBJECT))
        record = logs.only("token_version_lookup_unwired")
        assert record["user_ref"] == subject_log_ref(SUBJECT)
        assert SUBJECT not in str(record)

    def test_tenant_lookup_unwired_does_not_leak(self) -> None:
        from src.app.core.security import jwt_service

        with (
            patch.object(jwt_service, "_user_tenant_lookup", None),
            patch("src.app.core.tenancy.context.get_current_tenant", return_value=None),
            _recording_logs(*self.LOGGER.split(".", 1)) as logs,
        ):
            assert asyncio.run(_resolve_tenant_id_for_token({"sub": SUBJECT}, MagicMock())) is None
        record = logs.only("user_tenant_lookup_unwired")
        assert record["user_ref"] == subject_log_ref(SUBJECT)
        assert SUBJECT not in str(record)

    def test_token_version_cache_read_error_does_not_leak(self) -> None:
        """The Redis-read failure names the user whose version it wanted."""
        redis = _fake_redis(get=AsyncMock(side_effect=RuntimeError("redis boom")))
        with (
            patch(
                "src.app.core.security.jwt_utils.decode_verified_token",
                new=AsyncMock(return_value=_jwt_payload()),
            ),
            patch(
                "src.app.core.security.jwt_service.is_token_blacklisted",
                new=AsyncMock(return_value=False),
            ),
            patch("src.app.core.cache.client.cache_client", redis),
            _recording_logs(*self.LOGGER.split(".", 1)) as logs,
        ):
            asyncio.run(verify_token("tok", TokenType.ACCESS, MagicMock()))
        record = logs.only("token_version_cache_read_error")
        assert record["user_ref"] == subject_log_ref(SUBJECT)
        assert SUBJECT not in str(record)

    def test_token_version_cache_write_error_does_not_leak(self) -> None:
        from src.app.core.security import jwt_service

        with (
            patch.object(jwt_service, "_token_version_lookup", AsyncMock(return_value=5)),
            patch(
                "src.app.core.cache.client.cache_client.set_nx",
                new=AsyncMock(side_effect=RuntimeError("write boom")),
            ),
            _recording_logs(*self.LOGGER.split(".", 1)) as logs,
        ):
            assert asyncio.run(jwt_service._load_token_version("cache-key", SUBJECT)) == 5
        record = logs.only("token_version_cache_write_error")
        assert record["user_ref"] == subject_log_ref(SUBJECT)
        assert SUBJECT not in str(record)

    def test_token_version_lookup_failure_does_not_leak(self) -> None:
        """The DB-lookup failure rejects the token (fail closed) — the event
        an incident actually greps for."""
        redis = _fake_redis(get=AsyncMock(return_value=None))
        with (
            patch(
                "src.app.core.security.jwt_utils.decode_verified_token",
                new=AsyncMock(return_value=_jwt_payload()),
            ),
            patch(
                "src.app.core.security.jwt_service.is_token_blacklisted",
                new=AsyncMock(return_value=False),
            ),
            patch("src.app.core.cache.client.cache_client", redis),
            patch(
                "src.app.core.security.jwt_service._load_token_version",
                AsyncMock(side_effect=RuntimeError("db boom")),
            ),
            _recording_logs(*self.LOGGER.split(".", 1)) as logs,
        ):
            # Fail closed: the token is rejected.
            assert asyncio.run(verify_token("tok", TokenType.ACCESS, MagicMock())) is None
        record = logs.only("token_version lookup failed, rejecting")
        assert record["user_ref"] == subject_log_ref(SUBJECT)
        assert SUBJECT not in str(record)

    def test_no_subject_under_any_key_in_verify_token_records(self) -> None:
        """Whole-path invariant: no record on this path carries a raw subject
        under ANY of the identity keys, whatever the structlog config is."""
        redis = _fake_redis(get=AsyncMock(side_effect=RuntimeError("redis boom")))
        with (
            patch(
                "src.app.core.security.jwt_utils.decode_verified_token",
                new=AsyncMock(return_value=_jwt_payload()),
            ),
            patch(
                "src.app.core.security.jwt_service.is_token_blacklisted",
                new=AsyncMock(return_value=False),
            ),
            patch("src.app.core.cache.client.cache_client", redis),
            _recording_logs(*self.LOGGER.split(".", 1)) as logs,
        ):
            asyncio.run(verify_token("tok", TokenType.ACCESS, MagicMock()))
        assert logs.events, "verify_token emitted no records"
        for record in logs.events:
            for key in ("user", "username", "email", "sub"):
                assert key not in record, f"raw subject leaked under {key!r}: {record}"


class TestDirectoryLogSitesCarryNoPII:
    """The two cache-hit debug events in ``users/directory.py``."""

    LOGGER = ("src.app.modules.users.directory", "logger")

    @staticmethod
    def _cached_user() -> dict[str, Any]:
        return _cached_user_payload()

    @pytest.mark.asyncio
    async def test_by_username_cache_hit_logs_a_reference(self) -> None:
        from src.app.modules.users.directory import UserDirectory

        with (
            patch(
                "src.app.modules.users.directory.UserCacheInvalidator.get_cached_user",
                new=AsyncMock(return_value=self._cached_user()),
            ),
            _recording_logs(*self.LOGGER) as logs,
        ):
            await UserDirectory(MagicMock()).by_username(BARE_SUBJECT)
        record = logs.only("user_lookup_cache_hit")
        assert record["lookup"] == "by_username"
        assert record["user_ref"] == subject_log_ref(BARE_SUBJECT)
        assert BARE_SUBJECT not in str(record)

    @pytest.mark.asyncio
    async def test_by_email_cache_hit_logs_a_reference(self) -> None:
        from src.app.modules.users.directory import UserDirectory

        with (
            patch(
                "src.app.modules.users.directory.UserCacheInvalidator.get_cached_user",
                new=AsyncMock(return_value=self._cached_user()),
            ),
            _recording_logs(*self.LOGGER) as logs,
        ):
            await UserDirectory(MagicMock()).by_email(SUBJECT)
        record = logs.only("user_lookup_cache_hit")
        assert record["lookup"] == "by_email"
        assert record["user_ref"] == subject_log_ref(SUBJECT)
        assert SUBJECT not in str(record)
        assert "corp.example.com" not in str(record), "partial masking still named the employer"


class TestFailOpenKnobWarnings:
    """The knobs are legal; their silence was not."""

    def test_default_configuration_is_quiet(self) -> None:
        """A warning on every boot trains operators to ignore startup logs."""
        _, warnings = _build_with_warnings()
        assert warnings == [], f"secure defaults still warned: {warnings}"

    @pytest.mark.parametrize(
        ("field", "opened", "secure"),
        FAIL_OPEN_KNOBS,
        ids=[knob[0] for knob in FAIL_OPEN_KNOBS],
    )
    def test_each_opened_knob_warns(self, field: str, opened: Any, secure: Any) -> None:
        _, warnings = _build_with_warnings(**{field: opened})
        assert len(warnings) == 1, f"{field} opened but produced {len(warnings)} warnings"
        assert field in warnings[0]
        # Name the knob AND its cost: the field description already stated the
        # flag, so a bare name would repeat the settings doc, not inform.
        consequence = warnings[0].split(f"{field}:", 1)[1].split(",", 1)[0].strip()
        assert len(consequence) > 20, f"{field} warns without saying what it costs: {consequence!r}"

    @pytest.mark.parametrize(
        ("field", "opened", "secure"),
        FAIL_OPEN_KNOBS,
        ids=[knob[0] for knob in FAIL_OPEN_KNOBS],
    )
    def test_secure_value_does_not_warn(self, field: str, opened: Any, secure: Any) -> None:
        _, warnings = _build_with_warnings(**{field: secure})
        assert warnings == [], f"{field} at its secure default {secure!r} warned"

    def test_one_aggregate_warning_for_all_knobs(self) -> None:
        """One line answers 'what did this deployment open?'."""
        _, warnings = _build_with_warnings(**{field: opened for field, opened, _ in FAIL_OPEN_KNOBS})
        assert len(warnings) == 1
        for field, _, _ in FAIL_OPEN_KNOBS:
            assert field in warnings[0]

    def test_never_raises(self) -> None:
        """Fail-open is a deliberate availability trade (see the S-1 note on
        TOKEN_CACHE_FAILURE_MODE). The validator's job is to be loud, not to
        overturn the operator's choice."""
        settings, warnings = _build_with_warnings(**{field: opened for field, opened, _ in FAIL_OPEN_KNOBS})
        assert len(warnings) == 1
        assert settings.MFA_LOCKOUT_FAIL_CLOSED is False

    def test_warning_survives_the_structlog_sanitizer(self) -> None:
        """Knob names are configuration, not PII — the sanitizer must not eat
        the line that tells an operator what they turned on."""
        _, warnings = _build_with_warnings(TOKEN_CACHE_FAILURE_MODE="fail_open")
        assert LogSanitizer.sanitize_message(warnings[0]) == warnings[0]
