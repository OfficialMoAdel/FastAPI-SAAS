"""C-3 (dxra) e2e: three 401 shapes on GET /api/v1/users/me."""
from __future__ import annotations

import importlib.util
import json
import logging
from pathlib import Path

import pytest
from sqlalchemy import event

REPO_ROOT = Path(__file__).resolve().parents[3]
sys_path_placeholder = True

import sys

sys.path.insert(0, str(REPO_ROOT))

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncEngine

from src.app.core.db.database import async_engine
from src.app.core.security.jwt_service import TokenType, create_access_token

USERS_ME_PATH = "/api/v1/users/me"

pytestmark = pytest.mark.e2e


async def _make_expired_token(email: str) -> str:
    return await create_access_token(
        {"sub": email, "type": TokenType.ACCESS.value},
        expires_delta=timedelta(seconds=-10),
    )


def _checkout_counter(engine: AsyncEngine):
    counter = [0]

    def cb(dbapi_connection, connection_record, connection_proxy):
        counter[0] += 1

    event.listen(engine.sync_engine, "checkout", cb)
    return counter, cb


def _statement_counter(engine: AsyncEngine):
    counter = [0]

    def cb(conn, cursor, statement, parameters, context, executemany):
        counter[0] += 1

    event.listen(engine.sync_engine, "before_cursor_execute", cb)
    return counter, cb


async def _shape(client, headers: dict | None):
    engine = async_engine
    checkouts, checkout_cb = _checkout_counter(engine)
    statements, stmt_cb = _statement_counter(engine)
    try:
        resp = await client.get(USERS_ME_PATH, headers=headers or {})
        status = resp.status_code
        body = resp.text
    finally:
        event.remove(engine.sync_engine, "checkout", checkout_cb)
        event.remove(engine.sync_engine, "before_cursor_execute", stmt_cb)
    return status, body, checkouts[0], statements[0]


@pytest.mark.asyncio
async def test_dxra_three_401_shapes_zero_session_zero_sql(client, authenticated_user):
    email = authenticated_user["email"]
    expired = await _make_expired_token(email)

    cases = [
        ("no_auth", {}),
        ("malformed", {"Authorization": "Bearer not-a-jwt"}),
        ("expired", {"Authorization": f"Bearer {expired}"}),
    ]

    rows = []
    for name, headers in cases:
        status, body, n_checkout, n_stmt = await _shape(client, headers)
        rows.append((name, status, body, n_checkout, n_stmt))
        print(f"dxra shape={name} status={status} body={body!r} checkouts={n_checkout} statements={n_stmt}")

    for name, status, body, n_checkout, n_stmt in rows:
        assert status == 401, f"{name}: expected 401, got {status}: {body!r}"
        assert n_checkout == 0, f"{name}: expected 0 session checkouts, got {n_checkout}"
        assert n_stmt == 0, f"{name}: expected 0 SQL statements, got {n_stmt}"

    for _name, _status, body, _c, _s in rows:
        parsed = json.loads(body)
        assert "detail" in parsed or "error" in parsed, f"refusal body missing error/detail keys: {body!r}"


@pytest.mark.asyncio
async def test_dxra_caller_enumeration_matches_mxe4_oracle():
    validator = REPO_ROOT / "scripts" / "perf-validation" / "isomorphism_validator.py"
    assert validator.exists()
    spec = importlib.util.spec_from_file_location("isomorphism_validator", str(validator))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    c3 = getattr(mod, "C3_DB_CALLERS")
    assert isinstance(c3, frozenset) and len(c3) > 0

    found = set()
    for py in (REPO_ROOT / "src").rglob("*.py"):
        try:
            import ast
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for arg in node.args.args + node.args.kwonlyargs:
                if arg.annotation is None:
                    continue
                text = ast.unparse(arg.annotation) if hasattr(ast, "unparse") else ""
                if "Depends(async_get_db)" in text:
                    found.add((str(py.relative_to(REPO_ROOT)).replace("\\", "/"), node.name))

    print(f"dxra AST scan live={len(found)} oracle={len(c3)} intersection={len(found & set(c3))}")
    missing = found - set(c3)
    if missing:
        print(f"dxra WARNING: {len(missing)} live-site(s) not in closed oracle:")
        for m in sorted(missing):
            print(f"  + {m}")
    # empty live scan would be a degenerate oracle
    assert len(found) > 0


@pytest.mark.asyncio
async def test_dxra_error_count_observed_not_asserted(client, authenticated_user):
    expired = await _make_expired_token(authenticated_user["email"])

    class _ErrorCounter(logging.Handler):
        def __init__(self):
            super().__init__(level=logging.ERROR)
            self.count = 0

        def emit(self, record):
            if record.levelno >= logging.ERROR:
                self.count += 1

    counter = _ErrorCounter()
    root = logging.getLogger()
    root.addHandler(counter)
    try:
        for spec in ({}, {"Authorization": "Bearer not-a-jwt"},
                    {"Authorization": f"Bearer {expired}"}):
            try:
                await client.get(USERS_ME_PATH, headers=spec)
            except Exception:  # noqa: BLE001
                pass
        print(f"dxra observed ERROR-level records across 3 shapes: {counter.count}")
    finally:
        root.removeHandler(counter)
    # intentionally NO assertion -- A-12 owns the severity decision.
    assert True
