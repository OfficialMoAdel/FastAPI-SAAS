"""io15.8: every mutating endpoint declares its response shape, or says why not.

`response_model` is a filter, not a comment. FastAPI validates the handler's
return value through it and drops every field the model does not declare, so an
absent model means two things at once: the OpenAPI schema says nothing, and
nothing in CI would notice if a handler started returning something new. A
model that is too NARSE is worse than no model, because it silently deletes
fields clients already parse.

This gate walks every router under `src/app/modules/` and `src/app/api/` and
fails on a mutating endpoint (POST/PUT/PATCH/DELETE) that declares neither
`response_model` nor an entry in `ALLOWLIST`.

Three classes of endpoint cannot carry a `response_model`, and they are
allow-listed for structurally different reasons rather than one blanket excuse:

1. **204 No Content.** FastAPI asserts `is_body_allowed_for_status_code` for
   any route that declares a `response_model`, so `response_model=Anything` on
   a 204 raises `AssertionError: Status code 204 must not have a response body`
   at import time (verified against fastapi 0.115.14,
   `fastapi/routing.py::APIRoute.__init__`). There is no correct annotation
   for these; the handler's `-> None` return is the declaration.
2. **A returned `Response` / `StreamingResponse`.** FastAPI returns these
   verbatim and never runs them through the response model, so a
   `response_model` would document a payload the client never receives --
   worse than silence, because the OpenAPI schema would then be a lie.
3. **A genuinely dynamic body.** A service dict whose field set is decided at
   runtime cannot be modelled faithfully, and guessing wrong strips fields.

Class 3 is the one that can rot, so those entries are individually reasoned
below. Removing any entry turns the debt into a CI failure, which is the
point: an exception is only allowed to be removed deliberately.

Usage:
    uv run python scripts/checks/mutation_response_models.py
    uv run python scripts/checks/mutation_response_models.py --list-allowlist

Exit codes:
    0 - every mutating endpoint declares a response model (or is allow-listed)
    1 - one or more mutating endpoints are undeclared and not allow-listed
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

# Repo root = two levels up (scripts/checks/<gate>.py)
ROOT = Path(__file__).resolve().parents[2]
MODULES_DIR = ROOT / "src" / "app" / "modules"
API_DIR = ROOT / "src" / "app" / "api"

MUTATING_METHODS = frozenset({"post", "put", "patch", "delete"})

#: Mutating endpoints that legitimately carry no `response_model`, each with the
#: structural reason, grouped by KIND so the dominant reasons stay visible as
#: counts rather than as one flat list of 22 exceptions.
#:
#: KEY is ``"path:function_name"``. A key stops resolving the moment its
#: handler is renamed or moved, and a stale key is reported as a failure rather
#: than ignored, so an exception can never silently start covering a different
#: endpoint. Line numbers are deliberately NOT part of the key: they shift
#: whenever an unrelated import is added above the handler, and a gate that
#: fails on unrelated edits teaches people to bulk-rewrite the allow-list
#: instead of reading it.
ALLOWLIST: dict[str, str] = {
    # --- 204 No Content: FastAPI forbids response_model on a bodyless status.
    # Each of these is `status_code=204` and returns `None` (or a bare 204
    # `Response`), so a model is an import-time AssertionError, not a style nit.
    # The `tiers` and `rate_limits` routers DO declare `response_model` -- on
    # their 200-status routes, not on these.
    "src/app/api/v1/admin/jobs.py:clear_dlq": (
        "DELETE /dlq is 204; FastAPI rejects a response_model on a bodyless status"
    ),
    "src/app/modules/auth/routers/passkey.py:delete_credential": (
        "DELETE /credentials/{id} is 204; step-up verification precedes the erasure"
    ),
    "src/app/modules/billing/router.py:delete_subscription_plan": (
        "DELETE /plans/{id} is 204; the plan is deactivated, not echoed back"
    ),
    "src/app/modules/feature_flags/router.py:delete_feature_flag": (
        "DELETE /{flag_name} is 204 and returns a bare 204 Response"
    ),
    "src/app/modules/notifications/router.py:delete_template_override": (
        "DELETE /templates/overrides/{name} is 204; a missing row is already a 404"
    ),
    "src/app/modules/posts/router.py:erase_post": (
        "DELETE /by-user/{username}/{id} is 204 soft delete, returns bare Response"
    ),
    "src/app/modules/posts/router.py:erase_db_post": (
        "DELETE /by-user/{username}/db/{id} is 204 hard delete, returns bare Response"
    ),
    "src/app/modules/rbac/router.py:delete_role": (
        "DELETE /{role_id} is 204; a system role is rejected by the service, not echoed"
    ),
    "src/app/modules/rbac/router.py:add_permission_to_role": (
        "POST /{role_id}/permissions/{slug} is 204 and idempotent -- there is no created"
        " object and no changed state to report"
    ),
    "src/app/modules/rbac/router.py:remove_permission_from_role": (
        "DELETE /{role_id}/permissions/{slug} is 204; the absent grant is the result"
    ),
    "src/app/modules/rbac/router.py:assign_role_to_user": (
        "POST /{role_id}/users/{user_id} is 204 and idempotent -- reassigning an existing"
        " grant is the same 204 as creating one, so a body claiming 'assigned' would be"
        " true in both cases and informative in neither"
    ),
    "src/app/modules/rbac/router.py:remove_role_from_user": (
        "DELETE /{role_id}/users/{user_id} is 204; the absent grant is the result"
    ),
    "src/app/modules/storage/routers/files.py:delete_file": (
        "DELETE /{file_id} is 204 and declares response_class=Response alongside it"
    ),
    "src/app/modules/user_preferences/router.py:delete_profile_image": (
        "DELETE /image is 204; the cleared profile URL is observable via GET /image"
    ),
    "src/app/modules/webhooks/router.py:delete_webhook_endpoint": (
        "DELETE /{endpoint_id} is 204; a missing endpoint is already a 404 above it"
    ),
    # --- Returns a Response subclass: a model would document a body never sent.
    "src/app/modules/ai/router.py:chat_completion_stream": (
        "POST /chat/stream returns a StreamingResponse (text/event-stream). The body is an"
        " SSE frame stream whose per-chunk shape is decided by the model client, so no JSON"
        " model can describe it and a response_model would appear in the OpenAPI schema as a"
        " body the client never receives"
    ),
    "src/app/modules/auth/routers/sso.py:handle_post_callback": (
        "POST /{provider}/callback returns the ORJSONResponse built by"
        " _access_only_response, which also transports Set-Cookie headers; the field set is"
        " decided by the AuthTokenResponse|dict union that process_callback returns, not by a"
        " fixed schema"
    ),
    "src/app/modules/auth/routers/sso.py:connect_provider": (
        "POST /{provider}/connect returns a RedirectResponse to the provider's authorization"
        " URL; there is no body to model"
    ),
    # --- Dynamic body: no faithful model exists, and a wrong one strips fields.
    "src/app/modules/billing/routers/products.py:delete_product": (
        "DELETE /{product_id} returns dict(deactivate_product()) -- a full Product row re-read"
        " from the DB, or {} when that re-read returns None (billing/service.py). The {}"
        " branch is a real, reachable second shape, so a ProductRead|dict union would advertise"
        " a payload the endpoint does not reliably send"
    ),
}


@dataclass(frozen=True)
class Undeclared:
    """One mutating endpoint with no `response_model` and no allow-list entry."""

    rel: str
    line: int
    method: str
    func: str

    def __str__(self) -> str:
        return f"{self.rel}:{self.line}: {self.method.upper()} {self.func}() has no response_model"


@dataclass(frozen=True)
class StaleEntry:
    """An allow-list key that no longer resolves to a live undeclared endpoint."""

    key: str

    def __str__(self) -> str:
        return (
            f"stale allow-list entry: {self.key} -- the handler was renamed, moved, or given a"
            f" response_model. Delete the entry if the endpoint is now annotated."
        )

def router_files() -> list[Path]:
    """Every router module the gate governs, in a stable order."""
    found = {p.resolve() for p in MODULES_DIR.rglob("*.py") if p.name != "__init__.py"}
    found |= {p.resolve() for p in API_DIR.rglob("*.py") if p.name != "__init__.py"}
    return sorted(found)


def scan() -> tuple[list[Undeclared], set[str]]:
    """Return the undeclared mutating endpoints and the keys the allow-list covers.

    Static analysis only: no import, no database, no network, so the gate costs
    one tree walk and can run in the lint job.
    """
    undeclared: list[Undeclared] = []
    covered: set[str] = set()

    for path in router_files():
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                    continue
                if dec.func.attr not in MUTATING_METHODS:
                    continue
                if any(kw.arg == "response_model" for kw in dec.keywords):
                    continue
                key = f"{rel}:{node.name}"
                if key in ALLOWLIST:
                    covered.add(key)
                    continue
                undeclared.append(Undeclared(rel, node.lineno, dec.func.attr, node.name))

    return undeclared, covered


def check() -> list[Undeclared | StaleEntry]:
    """Return every way the gate is currently unsatisfied."""
    undeclared, covered = scan()
    problems: list[Undeclared | StaleEntry] = list(undeclared)
    for key in ALLOWLIST:
        if key not in covered:
            problems.append(StaleEntry(key))
    # Undeclared endpoints first: they are what the reader came to fix, and a
    # stale entry is bookkeeping. The two classes share no comparable fields,
    # so the key is explicit rather than dataclass `order=True` (which returns
    # NotImplemented across unrelated classes and raises on sort).
    return sorted(problems, key=lambda p: (isinstance(p, StaleEntry), str(p)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--list-allowlist", action="store_true", help="print the allow-list with reasons and exit"
    )
    args = parser.parse_args(argv)

    if args.list_allowlist:
        for key, reason in sorted(ALLOWLIST.items()):
            print(f"{key}\n    {reason}")
        return 0

    problems = check()
    if not problems:
        print(
            f"OK: every mutating endpoint declares a response_model "
            f"({len(ALLOWLIST)} reasoned exceptions)"
        )
        return 0

    for problem in problems:
        print(f"FAIL: {problem}", file=sys.stderr)
    print(
        f"\n{len(problems)} problem(s). Either declare a response_model that matches what"
        f"\nthe handler actually returns, or add a reasoned entry to ALLOWLIST in"
        f"\n{Path(__file__).name} and say WHY the endpoint cannot carry one.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
