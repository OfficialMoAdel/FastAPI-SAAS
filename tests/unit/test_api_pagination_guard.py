"""R3-02: API surface guard — list endpoints must use shared pagination.

R1-10 standardized billing + audit_logs on ``get_pagination_params``.
This test prevents drift: raw ``page = Query(...)`` / ``per_page = Query(...)``
outside ``core/pagination.py`` is a regression.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def test_no_raw_page_query_params_in_routers():
    offenders: list[str] = []
    for router in (ROOT / "src/app/modules").rglob("router.py"):
        text = router.read_text(encoding="utf-8")
        # raw Query for page/per_page (le=, ge=) outside the shared dependency
        if re.search(r"\bpage\s*:\s*int\s*=\s*Query\(.*\bge=.*le=", text):
            offenders.append(str(router.relative_to(ROOT)))
        # also catch per_page raw Query with le=1000 (> MAX)
        if re.search(r"\bper_page\s*:\s*int\s*=\s*Query\(.*le=1000", text):
            offenders.append(str(router.relative_to(ROOT)) + " (per_page le=1000)")
    assert not offenders, "Routers must use get_pagination_params, not raw Query:\n" + "\n".join(offenders)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
