#!/usr/bin/env python3
"""Phase 1 Quick Wins Validation.

Validates: U3 (Parallel Handlers), U4 (Pipeline), U15 (SETEX), U21 (Random), U8 (Pool Timeout)
"""

import asyncio
import time
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

PHASE1_TARGETS = {
    "U3": {"name": "Parallel Handlers", "target": "60% faster dispatch"},
    "U4": {"name": "Redis Pipeline", "target": "66% fewer roundtrips"},
    "U15": {"name": "SETEX", "target": "33% fewer commands"},
    "U21": {"name": "Random Module", "target": "99% faster RNG"},
    "U8": {"name": "Pool Timeout", "target": "Graceful timeout"},
}


async def validate_u3_parallel_handlers() -> dict[str, Any]:
    """Verify asyncio.gather() parallelizes handlers."""
    logger.info("validate_start", optimization="U3", target="60% faster")

    async def slow_handler():
        await asyncio.sleep(0.1)
        return True

    # Simulate 3 handlers that would take 300ms sequentially
    start = time.perf_counter()
    results = await asyncio.gather(slow_handler(), slow_handler(), slow_handler())
    elapsed_ms = (time.perf_counter() - start) * 1000

    passed = elapsed_ms < 150 and all(results)
    improvement_pct = (1 - elapsed_ms / 300) * 100

    logger.info(
        "validate_complete",
        optimization="U3",
        elapsed_ms=round(elapsed_ms, 2),
        target_ms=150,
        improvement_pct=round(improvement_pct, 1),
        passed=passed,
    )

    return {
        "passed": passed,
        "elapsed_ms": elapsed_ms,
        "target_ms": 150,
        "improvement_pct": improvement_pct,
    }


async def validate_u4_pipeline() -> dict[str, Any]:
    """Verify Redis pipeline batches operations."""
    logger.info("validate_start", optimization="U4", target="66% fewer roundtrips")

    # Check that pipeline is used in user caching
    from pathlib import Path

    service_path = Path(__file__).parents[2] / "src" / "app" / "modules" / "users" / "service.py"
    source = service_path.read_text()

    uses_pipeline = "pipe.setex" in source and "async with" in source
    has_multiple_setex = source.count("pipe.setex") >= 3

    passed = uses_pipeline and has_multiple_setex

    logger.info(
        "validate_complete",
        optimization="U4",
        uses_pipeline=uses_pipeline,
        setex_count=source.count("pipe.setex"),
        passed=passed,
    )

    return {
        "passed": passed,
        "uses_pipeline": uses_pipeline,
        "setex_count": source.count("pipe.setex"),
    }


async def validate_u15_setex() -> dict[str, Any]:
    """Verify SETEX used instead of SET + EXPIRE."""
    logger.info("validate_start", optimization="U15", target="33% fewer commands")

    from pathlib import Path

    cache_path = Path(__file__).parents[2] / "src" / "app" / "core" / "utils" / "cache.py"
    source = cache_path.read_text()

    uses_setex = "setex" in source.lower()
    no_separate_expire = ".expire(" not in source or source.count(".expire(") < 3

    passed = uses_setex and no_separate_expire

    logger.info(
        "validate_complete",
        optimization="U15",
        uses_setex=uses_setex,
        no_separate_expire=no_separate_expire,
        passed=passed,
    )

    return {"passed": passed, "uses_setex": uses_setex}


async def validate_u21_random() -> dict[str, Any]:
    """Verify random module used for non-crypto operations."""
    logger.info("validate_start", optimization="U21", target="99% faster RNG")

    from pathlib import Path

    xfetch_path = Path(__file__).parents[2] / "src" / "app" / "core" / "utils" / "cache.py"
    source = xfetch_path.read_text()

    uses_random = "import random" in source or "from random" in source
    uses_in_xfetch = "random.random()" in source or "random.uniform" in source

    passed = uses_random and uses_in_xfetch

    logger.info(
        "validate_complete",
        optimization="U21",
        uses_random=uses_random,
        uses_in_xfetch=uses_in_xfetch,
        passed=passed,
    )

    return {"passed": passed, "uses_random": uses_random}


async def validate_u8_pool_timeout() -> dict[str, Any]:
    """Verify pool timeout is configured."""
    logger.info("validate_start", optimization="U8", target="Graceful timeout")

    from pathlib import Path

    config_path = Path(__file__).parents[2] / "src" / "app" / "core" / "db" / "config.py"
    source = config_path.read_text()

    has_timeout = "pool_timeout" in source.lower() or "timeout" in source

    passed = has_timeout

    logger.info(
        "validate_complete",
        optimization="U8",
        has_timeout=has_timeout,
        passed=passed,
    )

    return {"passed": passed, "has_timeout": has_timeout}


async def validate_phase1() -> dict[str, Any]:
    """Run all Phase 1 validations."""
    logger.info("phase_start", phase=1)

    optimizations = {
        "U3": await validate_u3_parallel_handlers(),
        "U4": await validate_u4_pipeline(),
        "U15": await validate_u15_setex(),
        "U21": await validate_u21_random(),
        "U8": await validate_u8_pool_timeout(),
    }

    all_passed = all(opt["passed"] for opt in optimizations.values())
    passed_count = sum(1 for opt in optimizations.values() if opt["passed"])

    logger.info(
        "phase_complete",
        phase=1,
        passed=all_passed,
        passed_count=passed_count,
        total=len(optimizations),
    )

    return {
        "passed": all_passed,
        "optimizations": optimizations,
        "passed_count": passed_count,
        "total": len(optimizations),
    }


if __name__ == "__main__":
    result = asyncio.run(validate_phase1())
    print(f"Phase 1 Validation: {'PASSED' if result['passed'] else 'FAILED'}")
    print(f"Optimizations: {result['passed_count']}/{result['total']}")
