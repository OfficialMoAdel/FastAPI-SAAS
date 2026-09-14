"""Deletion-test boundary for the JWT authority split (P2-#9).

Plan #9 mandate: one token authority. The fold direction is "jwt_service
absorbs jwt_utils without touching callers". Today the boundary is already
clean — jwt_service OWNS encode + policy, jwt_utils OWNS decode + key
management, and issuance has a single door (TokenIssuer.issue_session).
These tests pin that contract so a future physical fold (or a regression
that reintroduces duplicated decode/encode logic) is caught immediately.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import ModuleType

import pytest

from src.app.core.security import jwt_service, jwt_utils
from src.app.modules.auth.services import tokens as auth_tokens


def _source_of(module: ModuleType) -> str:
    path = inspect.getsourcefile(module)
    assert path is not None, f"cannot locate source for {module!r}"
    return Path(path).read_text(encoding="utf-8")


_jwt_service_src = _source_of(jwt_service)
_jwt_utils_src = _source_of(jwt_utils)


def _code_only(source: str) -> str:
    """Strip comments and string literals so greps hit code, not prose."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Module):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


class TestDeletionTestFoldDirection:
    """jwt_service could absorb jwt_utils without touching callers — because
    it already imports every decode duty from jwt_utils via fn-scope seams
    and duplicates nothing."""

    def test_jwt_service_never_calls_jwtdirectly_decode(self) -> None:
        """jwt_service must not duplicate the decode path: no direct
        jwt.decode() call — all decodes route through jwt_utils."""
        assert "jwt.decode(" not in _code_only(_jwt_service_src)

    def test_jwt_utils_never_calls_jwtencode(self) -> None:
        """jwt_utils is decode-only; encoding authority stays in jwt_service."""
        code = _code_only(_jwt_utils_src)
        assert "jwt.encode(" not in code
        # The single decode call site lives here and only here.
        assert "jwt.decode(" in code

    def test_decode_verified_token_is_the_single_decode_entry(self) -> None:
        """The ONE decode path exists in jwt_utils and is exported; every
        jwt_service decode consumer references it (fn-scope, late-binding)."""
        assert hasattr(jwt_utils, "decode_verified_token")
        service_code = _code_only(_jwt_service_src)
        assert service_code.count("decode_verified_token") >= 3  # verify_token + blacklist paths


class TestIssuanceSingleDoor:
    """TokenIssuer.issue_session stays the ONLY issuance door; the stable-sid
    invariant and RESERVED_CLAIMS protection must survive any fold."""

    @pytest.mark.asyncio
    async def test_issue_session_strips_dangerous_extra_claims(self) -> None:
        """R1-2: session/time claims in extra_claims must not reach the JWT —
        behavioral pin (replaces the deleted source-text pin)."""
        from unittest.mock import AsyncMock, patch

        from src.app.modules.auth.services.tokens import TokenIssuer

        user = {"id": 1, "username": "u", "email": "u@x.com", "tenant_id": None}
        with (
            patch(
                "src.app.modules.auth.services.tokens.TokenIssuer.create_token_pair",
                new_callable=AsyncMock,
                return_value=("a", "r"),
            ) as mock_pair,
            patch(
                "src.app.modules.auth.services.tokens.register_login_session",
                new_callable=AsyncMock,
            ),
        ):
            await TokenIssuer.issue_session(
                user,
                channel="password",
                subject="u",
                extra_claims={"sid": "forged", "absolute_exp": 1.0, "iat": 1.0, "email": "kept@x.com"},
            )
        token_data = mock_pair.await_args.args[0]
        assert token_data["sid"] != "forged"
        assert token_data.get("absolute_exp") != 1.0
        assert token_data.get("iat") != 1.0
        assert token_data["email"] == "kept@x.com"


    def test_issue_session_preserves_stable_sid_invariant(self) -> None:
        """The sid is minted once in issue_session and threaded into session
        registration without re-parse — the invariant a merge must not lose."""
        src = _source_of(auth_tokens)
        assert 'token_data.setdefault("sid", str(uuid4()))' in src
        assert 'session_id=token_data["sid"]' in src

    def test_issue_session_publishes_login_issued_at_seam(self) -> None:
        """P2-#8 seam: the single issuance door publishes the audit event."""
        src = _source_of(auth_tokens)
        assert "auth.login_issued" in src


class TestEncodeKeyManagementBoundary:
    """Key/algorithm management is shared, not duplicated: the symmetric
    check lives once (jwt_utils) and jwt_service consumes it."""

    def test_is_symmetric_algorithm_defined_once(self) -> None:
        assert hasattr(jwt_utils, "_is_symmetric_algorithm")
        # jwt_service consumes the helper; it does not re-derive HS-prefix logic.
        service_code = _code_only(_jwt_service_src)
        assert service_code.count("_is_symmetric_algorithm") >= 2
        assert "HS" not in service_code  # no duplicated algorithm-prefix logic
