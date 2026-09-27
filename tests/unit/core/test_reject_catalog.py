"""The middleware reject catalog presents outcomes THROUGH the exception families.

Card 13 removed the second declaration of the same HTTP outcome. A
``RejectRow`` used to carry ``(code, status)`` as literals, so
``rate_limited`` restated what ``RateLimitExceededError`` already declared
and ``tenant_access_denied`` restated ``ForbiddenError``'s 403 — a status
change could land in either place. A row now NAMES the family and
:meth:`RejectRow.resolve` reads the status and envelope code back off it
through ``resolve_http_status()`` / ``resolve_response_code()``, the same
hooks the one generic handler calls.

Two outcomes have no family (413 ``PAYLOAD_TOO_LARGE``, 400
``INVALID_INPUT``), so those rows declare a status and take the code from
``status_to_error_code``.

``middleware_reject`` itself is NOT removed: native ASGI middleware runs
before routing and cannot raise a domain exception into the app's handler, so
the adapter is what lets a guard reuse the family's presentation instead of
hand-rolling an envelope.
"""

import json

import pytest

from src.app.core.errors import (
    BAN_REASON_AUTO_BAN,
    BAN_REASON_MANUAL_BAN,
    REJECT_CATALOG,
    RejectRow,
    middleware_reject,
)
from src.app.shared.error_codes import ErrorCode, status_to_error_code
from src.app.shared.exceptions import (
    AppException,
    ForbiddenError,
    RateLimitExceededError,
    ServiceUnavailableError,
)
from src.app.shared.exceptions import ValidationError as DomainValidationError

# The pre-card-13 literals, verbatim. Every row must still resolve to exactly
# this pair — card 13 changed WHERE the pair comes from, never its value.
EXPECTED_PRESENTATION: dict[str, tuple[str, int]] = {
    "banned_ip": (ErrorCode.FORBIDDEN, 403),
    "admission_unavailable": (ErrorCode.SERVICE_UNAVAILABLE, 503),
    "internal_error": (ErrorCode.INTERNAL_ERROR, 500),
    "auto_banned": (ErrorCode.FORBIDDEN, 403),
    "rate_limited": (ErrorCode.RATE_LIMITED, 429),
    "tenant_header_missing": (ErrorCode.VALIDATION_ERROR, 400),
    "tenant_access_denied": (ErrorCode.FORBIDDEN, 403),
    "tenant_inactive": (ErrorCode.FORBIDDEN, 403),
    "payload_too_large": (ErrorCode.PAYLOAD_TOO_LARGE, 413),
    "invalid_input": (ErrorCode.INVALID_INPUT, 400),
    "attack_blocked": (ErrorCode.VALIDATION_ERROR, 400),
    "attack_banned": (ErrorCode.FORBIDDEN, 403),
    "bad_bot": (ErrorCode.FORBIDDEN, 403),
    "blacklisted_ip": (ErrorCode.FORBIDDEN, 403),
}

# Which family each row must name. Absence from this map is the *only* legal
# reason a row declares a status instead — it is how a newly added orphan
# outcome has to justify itself.
EXPECTED_FAMILY: dict[str, type[AppException]] = {
    "banned_ip": ForbiddenError,
    "admission_unavailable": ServiceUnavailableError,
    "internal_error": AppException,
    "auto_banned": ForbiddenError,
    "rate_limited": RateLimitExceededError,
    "tenant_header_missing": DomainValidationError,
    "tenant_access_denied": ForbiddenError,
    "tenant_inactive": ForbiddenError,
    "attack_blocked": DomainValidationError,
    "attack_banned": ForbiddenError,
    "bad_bot": ForbiddenError,
    "blacklisted_ip": ForbiddenError,
}

# The two rows with no exception family to name.
ORPHAN_KINDS = frozenset(EXPECTED_PRESENTATION) - frozenset(EXPECTED_FAMILY)


def _body(response) -> dict:
    return json.loads(response.body.decode())


class TestPresentationIsNotRestated:
    """The whole point: a row resolves code/status from ONE source."""

    def test_catalog_covers_every_kind(self) -> None:
        assert set(REJECT_CATALOG) == set(EXPECTED_PRESENTATION)

    @pytest.mark.parametrize("kind", sorted(EXPECTED_PRESENTATION))
    def test_resolved_presentation_is_unchanged(self, kind: str) -> None:
        """Card 13 moved the declaration, it did not change the envelope."""
        assert REJECT_CATALOG[kind].resolve() == EXPECTED_PRESENTATION[kind]

    @pytest.mark.parametrize("kind", sorted(EXPECTED_FAMILY))
    def test_row_names_its_exception_family(self, kind: str) -> None:
        assert REJECT_CATALOG[kind].error is EXPECTED_FAMILY[kind]
        assert REJECT_CATALOG[kind].status is None, (
            f"{kind} names a family AND a status — the two-source drift card 13 removed"
        )

    def test_only_the_two_orphan_outcomes_declare_a_status(self) -> None:
        """A new row must justify declaring a status instead of a family."""
        assert ORPHAN_KINDS == {"payload_too_large", "invalid_input"}
        for kind in ORPHAN_KINDS:
            assert REJECT_CATALOG[kind].error is None
            assert REJECT_CATALOG[kind].status is not None

    @pytest.mark.parametrize("kind", sorted(ORPHAN_KINDS))
    def test_orphan_rows_take_their_code_from_the_status_map(self, kind: str) -> None:
        status = REJECT_CATALOG[kind].status
        assert status is not None
        assert REJECT_CATALOG[kind].resolve() == (status_to_error_code(status), status)

    def test_row_with_neither_family_nor_status_fails_loud(self) -> None:
        """A half-declared row is a bug, not a default."""
        with pytest.raises(ValueError, match="neither an error family nor a status"):
            RejectRow("whoops").resolve()

    def test_resolve_reads_the_instance_hooks_not_the_class_attributes(self) -> None:
        """A family that derives its status per instance must still resolve.

        Reading ``http_status`` off the class would silently return the
        inherited 500 for such a family; going through the public resolve hook
        is what keeps the adapter honest.
        """

        class _Derived(RateLimitExceededError):
            def resolve_http_status(self) -> int:
                return 418

            def resolve_response_code(self) -> str:
                return ErrorCode.INTERNAL_ERROR

        assert RejectRow("teapot", error=_Derived).resolve() == (ErrorCode.INTERNAL_ERROR, 418)

    def test_catalog_is_immutable_at_runtime(self) -> None:
        """The security table must not be rewritable from anywhere.

        REJECT_CATALOG resolves every guard rejection, so a stray assignment
        would silently repoint a status/code/header family process-wide.
        The two sibling tables already carry this protection —
        principal.DENIAL_TABLE is a MappingProxyType and rbac.PermissionGraph
        is a frozen dataclass — this makes the catalog the third.
        """
        with pytest.raises(TypeError):
            REJECT_CATALOG["injected"] = None  # type: ignore[index]

        with pytest.raises(TypeError):
            del REJECT_CATALOG["banned_ip"]  # type: ignore[attr-defined]


class TestMiddlewareRejectAdapter:
    """``middleware_reject`` stays: pre-route middleware cannot raise."""

    def test_envelope_comes_from_the_family(self) -> None:
        response = middleware_reject("rate_limited")
        assert response.status_code == 429
        body = _body(response)
        assert body["error"]["code"] == ErrorCode.RATE_LIMITED
        assert body["error"]["message"] == "Rate limit exceeded"

    def test_orphan_row_presents_its_declared_status(self) -> None:
        response = middleware_reject("payload_too_large", max_size=2048)
        assert response.status_code == 413
        assert _body(response)["error"]["code"] == ErrorCode.PAYLOAD_TOO_LARGE
        assert response.headers["x-max-body-size"] == "2048"

    def test_message_template_still_resolves(self) -> None:
        response = middleware_reject("auto_banned", violation_count=4)
        assert "4 rate limit violations" in _body(response)["error"]["message"]

    def test_banned_ip_headers_come_from_primitives(self) -> None:
        response = middleware_reject("banned_ip", ban_reason="abuse", ban_expires_at=None)
        assert response.status_code == 403
        assert response.headers["X-Banned"] == "true"
        # The free-text admin reason must NOT be echoed; only the class is.
        assert response.headers["X-Ban-Reason"] == "manual_ban"
        # No expiry supplied -> no Retry-After, as before.
        assert "retry-after" not in {k.lower() for k in response.headers}

    @pytest.mark.parametrize(
        ("stored_reason", "expected"),
        [
            # auto_ban.py: "Auto-ban: <n> rate limit violations exceeded threshold of <t>"
            ("Auto-ban: 5 rate limit violations exceeded threshold of 4", "auto_ban"),
            # attack_detection.py: "Auto-banned for <type> attack"
            ("Auto-banned for sqli attack", "auto_ban"),
            # security/router.py manual ban: admin text
            ("ACME Corp breach, contact sec@acme.example", "manual_ban"),
            # security/router.py manual ban: the no-reason default
            ("Manual ban by admin 1", "manual_ban"),
            (None, "manual_ban"),
            ("", "manual_ban"),
        ],
    )
    def test_ban_reason_header_is_a_class_not_the_text(self, stored_reason: str | None, expected: str) -> None:
        """A 500-char admin string must never reach the banned client."""
        response = middleware_reject("banned_ip", ban_reason=stored_reason, ban_expires_at=None)
        value = response.headers["X-Ban-Reason"]
        assert value == expected
        assert value in {BAN_REASON_AUTO_BAN, BAN_REASON_MANUAL_BAN}
        if stored_reason:
            assert stored_reason not in value

    def test_long_admin_reason_is_not_reflected(self) -> None:
        secret = "INTERNAL-TICKET-4471 " + ("x" * 480)
        response = middleware_reject("banned_ip", ban_reason=secret, ban_expires_at=None)
        header = response.headers["X-Ban-Reason"]
        assert "INTERNAL-TICKET-4471" not in header
        assert len(header) <= 16

    def test_banned_ip_computes_retry_after_from_an_expiry(self) -> None:
        import time

        response = middleware_reject("banned_ip", ban_reason="abuse", ban_expires_at=time.time() + 30)
        assert 28 <= int(response.headers["Retry-After"]) <= 31

    def test_explicit_headers_merge_over_row_headers(self) -> None:
        response = middleware_reject(
            "rate_limited", headers={"Retry-After": "5", "X-RateLimit-Limit": "10"}
        )
        assert response.headers["Retry-After"] == "5"
        assert response.headers["X-RateLimit-Limit"] == "10"

    def test_unknown_kind_fails_loud(self) -> None:
        """A typo must never become a shapeless 500."""
        with pytest.raises(ValueError, match="Unknown middleware reject kind"):
            middleware_reject("rate_limitted")  # type: ignore[arg-type]
