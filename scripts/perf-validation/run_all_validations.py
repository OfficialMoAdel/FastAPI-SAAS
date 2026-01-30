#!/usr/bin/env python3
"""PERF-UNIQUE-R1 Complete Validation Suite.

Usage:
    python run_all_validations.py --phase all
    python run_all_validations.py --phase 1 --verbose
    python run_all_validations.py --compare baseline.json
    python run_all_validations.py --isomorphism-only

Outputs:
    - Console: Pass/Fail for each optimization
    - JSON: Detailed metrics in results/perf_validation_YYYYMMDD.json
    - HTML: Visual report in results/perf_report.html
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
from compare_results import compare_with_baseline
from isomorphism_validator import run_isomorphism_validation
from report_generator import generate_html_report
from validate_phase1 import validate_phase1
from validate_phase2 import validate_phase2
from validate_phase3 import validate_phase3
from validate_phase4 import validate_phase4

logger = structlog.get_logger(__name__)

RESULTS_DIR = Path(__file__).parent / "results"


async def run_phase_validation(phase: int) -> dict[str, Any]:
    """Run validation for a specific phase."""
    validators = {
        1: validate_phase1,
        2: validate_phase2,
        3: validate_phase3,
        4: validate_phase4,
    }

    if phase not in validators:
        raise ValueError(f"Invalid phase: {phase}")

    logger.info("phase_validation_start", phase=phase)
    result = await validators[phase]()
    logger.info(
        "phase_validation_complete",
        phase=phase,
        passed=result["passed"],
        optimizations_tested=len(result.get("optimizations", {})),
    )
    return result


async def main(args: argparse.Namespace) -> dict[str, Any]:
    """Main validation orchestrator."""
    start_time = datetime.now()
    logger.info("validation_suite_start", timestamp=start_time.isoformat(), args=vars(args))

    results: dict[str, Any] = {
        "timestamp": start_time.isoformat(),
        "phase_results": {},
        "isomorphism_results": {},
        "overall_pass": True,
        "metrics_summary": {},
        "execution_time_seconds": 0,
    }

    # Step 1: Run isomorphism tests first (behavior must not change)
    if not args.skip_isomorphism:
        logger.info("isomorphism_validation_start")
        iso_results = await run_isomorphism_validation()
        results["isomorphism_results"] = iso_results

        if not iso_results.get("all_passed", False):
            logger.error("isomorphism_validation_failed", failures=iso_results.get("failures", []))
            results["overall_pass"] = False
            if args.fail_fast:
                logger.error("fail_fast_triggered", reason="isomorphism_failure")
                return results

    # Step 2: Run performance validation by phase
    if args.isomorphism_only:
        logger.info("skipping_performance_validation", reason="isomorphism_only_mode")
    else:
        phases = [1, 2, 3, 4] if args.phase == "all" else [int(args.phase)]

        for phase in phases:
            try:
                phase_result = await run_phase_validation(phase)
                results["phase_results"][f"phase_{phase}"] = phase_result

                if not phase_result.get("passed", False):
                    results["overall_pass"] = False
                    if args.fail_fast:
                        logger.error("fail_fast_triggered", reason=f"phase_{phase}_failure")
                        break

            except Exception as e:
                logger.exception("phase_validation_error", phase=phase, error=str(e))
                results["phase_results"][f"phase_{phase}"] = {
                    "passed": False,
                    "error": str(e),
                }
                results["overall_pass"] = False

    # Step 3: Compare with baseline if provided
    if args.compare:
        logger.info("baseline_comparison_start", baseline=args.compare)
        comparison = compare_with_baseline(args.compare, results)
        results["baseline_comparison"] = comparison

    # Step 4: Calculate summary metrics
    results["metrics_summary"] = calculate_improvement_summary(results)

    # Step 5: Generate report
    results["execution_time_seconds"] = (datetime.now() - start_time).total_seconds()

    # Save JSON results
    RESULTS_DIR.mkdir(exist_ok=True)
    results_file = RESULTS_DIR / f"perf_validation_{start_time.strftime('%Y%m%d_%H%M%S')}.json"
    results_file.write_text(json.dumps(results, indent=2, default=str))
    logger.info("results_saved", path=str(results_file))

    # Generate HTML report
    if not args.no_report:
        report_path = generate_html_report(results, RESULTS_DIR)
        logger.info("report_generated", path=str(report_path))

    # Final summary
    logger.info(
        "validation_suite_complete",
        overall_pass=results["overall_pass"],
        execution_time_seconds=results["execution_time_seconds"],
        phases_tested=len(results["phase_results"]),
        summary=results["metrics_summary"],
    )

    return results


def calculate_improvement_summary(results: dict[str, Any]) -> dict[str, Any]:
    """Calculate aggregate improvement metrics."""
    summary = {
        "total_optimizations_tested": 0,
        "optimizations_passed": 0,
        "optimizations_failed": 0,
        "isomorphism_passed": results.get("isomorphism_results", {}).get("all_passed", False),
        "estimated_p95_improvement_pct": 0,
        "estimated_p99_improvement_pct": 0,
    }

    for phase_key, phase_result in results.get("phase_results", {}).items():
        for opt_key, opt_result in phase_result.get("optimizations", {}).items():
            summary["total_optimizations_tested"] += 1
            if opt_result.get("passed", False):
                summary["optimizations_passed"] += 1
            else:
                summary["optimizations_failed"] += 1

    # Calculate estimated improvements based on passed optimizations
    if summary["optimizations_passed"] >= 15:
        summary["estimated_p95_improvement_pct"] = 40
        summary["estimated_p99_improvement_pct"] = 55
    elif summary["optimizations_passed"] >= 10:
        summary["estimated_p95_improvement_pct"] = 30
        summary["estimated_p99_improvement_pct"] = 40
    elif summary["optimizations_passed"] >= 5:
        summary["estimated_p95_improvement_pct"] = 15
        summary["estimated_p99_improvement_pct"] = 20

    return summary


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="PERF-UNIQUE-R1 Validation Suite")
    parser.add_argument(
        "--phase",
        default="all",
        help="Phase to validate (1-4 or 'all')",
    )
    parser.add_argument(
        "--compare",
        help="Path to baseline JSON for comparison",
    )
    parser.add_argument(
        "--isomorphism-only",
        action="store_true",
        help="Only run isomorphism tests",
    )
    parser.add_argument(
        "--skip-isomorphism",
        action="store_true",
        help="Skip isomorphism tests",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop on first failure",
    )
    parser.add_argument(
        "--no-report",
        action="store_true",
        help="Skip HTML report generation",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose output",
    )
    return parser.parse_args()


if __name__ == "__main__":
    # Configure structured logging
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )

    args = parse_args()
    result = asyncio.run(main(args))

    sys.exit(0 if result["overall_pass"] else 1)
