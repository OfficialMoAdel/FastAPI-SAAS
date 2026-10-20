"""R2-04: module structure CI check.

Enforces the modular-monolith contract: every module under
``src/app/modules`` must contain ``router.py`` + ``service.py`` +
(``models.py`` or ``schemas.py``). Deviations are allowed only via the
explicit allow-list below, so new modules can't silently drift and the
allow-list itself is reviewed when it grows.

Usage:
    uv run python scripts/checks/module_structure.py

Exit codes:
    0 - all modules conform (or are allow-listed)
    1 - one or more modules violate the structure contract
"""

from __future__ import annotations

import sys
from pathlib import Path

# Repo root = two levels up (scripts/checks/<gate>.py)
ROOT = Path(__file__).resolve().parents[2]
MODULES_DIR = ROOT / "src" / "app" / "modules"

# Modules exempt from the canonical contract, with the reason. Adding a new
# entry here is a deliberate, reviewable decision — not an accident.
ALLOWLIST: dict[str, str] = {
    "_base": "shared BaseService/CRUD base classes, not an API module",
    "cache_warming": "background warming service, no HTTP surface or models",
    "health": "liveness/readiness router only, no service or models",
    "storage": "storage backend adapters (local/S3), no HTTP surface",
}

CANONICAL_REQUIRED = ("router.py", "service.py")
MODEL_REQUIRED_ANY = ("models.py", "schemas.py")


def check_module(module_dir: Path) -> list[str]:
    """Return a list of violations for one module directory (empty = OK)."""
    name = module_dir.name
    if name in ALLOWLIST:
        return []

    files = {f.name for f in module_dir.iterdir() if f.is_file()}
    violations: list[str] = []

    for required in CANONICAL_REQUIRED:
        if required not in files:
            violations.append(f"missing {required}")

    if not any(f in files for f in MODEL_REQUIRED_ANY):
        violations.append(f"missing one of {MODEL_REQUIRED_ANY}")

    return violations


def main() -> int:
    if not MODULES_DIR.is_dir():
        print(f"ERROR: modules directory not found: {MODULES_DIR}")
        return 1

    modules = sorted(
        d for d in MODULES_DIR.iterdir() if d.is_dir() and d.name != "__pycache__"
    )

    total = len(modules)
    allowlisted = [m.name for m in modules if m.name in ALLOWLIST]
    failing: list[tuple[str, list[str]]] = []

    for module_dir in modules:
        violations = check_module(module_dir)
        if violations:
            failing.append((module_dir.name, violations))

    print(f"Checked {total} modules under {MODULES_DIR.relative_to(ROOT)}")
    print(f"Allow-listed deviations ({len(allowlisted)}):")
    for name in allowlisted:
        print(f"  - {name}: {ALLOWLIST[name]}")

    # Warn if an allow-list entry no longer exists (stale allow-list)
    existing = {m.name for m in modules}
    stale = [name for name in ALLOWLIST if name not in existing]
    for name in stale:
        print(f"WARNING: allow-list entry '{name}' matches no module — remove it")

    if failing:
        print(f"\nFAIL: {len(failing)} module(s) violate the structure contract:")
        for name, violations in failing:
            print(f"  - {name}: {', '.join(violations)}")
        print(
            "\nFix: add the missing files, or add the module to ALLOWLIST in "
            "scripts/checks/module_structure.py with a justification."
        )
        return 1

    print("\nOK: all modules conform to the canonical structure.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
