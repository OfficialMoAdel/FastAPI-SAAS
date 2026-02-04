"""E2E Test Runner Script.

Runs all E2E tests in order, captures logs with timestamps, and generates HTML report.

Usage:
    uv run python scripts/run_e2e_tests.py
    uv run python scripts/run_e2e_tests.py --verbose
    uv run python scripts/run_e2e_tests.py --no-report

Output:
    - logs/e2e_test_YYYY-MM-DD_HH-MM-SS.log: Test execution log
    - reports/e2e_test_YYYY-MM-DD_HH-MM-SS.html: HTML test report (if --no-report not used)
"""

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Test execution order
TEST_ORDER = [
    "tests/e2e/test_tenant_isolation.py",
    "tests/e2e/test_file_upload.py",
    "tests/e2e/test_background_jobs.py",
]

# Exit codes
EXIT_SUCCESS = 0
EXIT_FAILURE = 1


def setup_directories() -> tuple[Path, Path]:
    """Create log and report directories."""
    log_dir = Path("logs")
    report_dir = Path("reports")
    log_dir.mkdir(exist_ok=True)
    report_dir.mkdir(exist_ok=True)
    return log_dir, report_dir


def get_timestamp() -> str:
    """Get current timestamp for filenames."""
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def run_test(test_file: str, verbose: bool) -> tuple[int, str, str]:
    """Run a single E2E test and return exit code, stdout, stderr."""
    cmd = ["uv", "run", "pytest", test_file, "-v"]
    if verbose:
        cmd.append("-vv")

    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


def generate_html_report(results: list[dict], report_path: Path) -> None:
    """Generate HTML test report."""
    passed = sum(1 for r in results if r["exit_code"] == 0)
    failed = len(results) - passed
    total_time = sum(r["duration"] for r in results)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>E2E Test Report</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            margin: 20px;
            background: #f5f5f5;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        h1 {{ color: #333; }}
        .summary {{ display: flex; gap: 20px; margin: 20px 0; }}
        .stat {{ background: #f0f0f0; padding: 15px; border-radius: 6px; flex: 1; text-align: center; }}
        .stat-value {{ font-size: 24px; font-weight: bold; }}
        .stat-label {{ color: #666; font-size: 14px; }}
        .passed {{ color: #28a745; }}
        .failed {{ color: #dc3545; }}
        .test {{ margin: 15px 0; padding: 15px; border-radius: 6px; border-left: 4px solid #ddd; }}
        .test.pass {{ border-left-color: #28a745; }}
        .test.fail {{ border-left-color: #dc3545; }}
        .test-name {{ font-weight: bold; margin-bottom: 5px; }}
        .test-duration {{ color: #666; font-size: 12px; }}
        .test-output {{
            background: #f8f8f8;
            padding: 10px;
            margin-top: 10px;
            border-radius: 4px;
            font-family: monospace;
            font-size: 12px;
            overflow-x: auto;
        }}
        .timestamp {{ color: #999; font-size: 12px; text-align: right; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>E2E Test Report</h1>
        <div class="timestamp">Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</div>

        <div class="summary">
            <div class="stat">
                <div class="stat-value">{total_time:.2f}s</div>
                <div class="stat-label">Total Duration</div>
            </div>
            <div class="stat">
                <div class="stat-value passed">{passed}</div>
                <div class="stat-label">Passed</div>
            </div>
            <div class="stat">
                <div class="stat-value failed">{failed}</div>
                <div class="stat-label">Failed</div>
            </div>
            <div class="stat">
                <div class="stat-value">{len(results)}</div>
                <div class="stat-label">Total Tests</div>
            </div>
        </div>
"""

    for result in results:
        status_class = "pass" if result["exit_code"] == 0 else "fail"
        status_text = "PASSED" if result["exit_code"] == 0 else "FAILED"
        output = result["stdout"] + result["stderr"]
        output_escaped = output.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        html += f"""
        <div class="test {status_class}">
            <div class="test-name">{result["test_file"]} - {status_text}</div>
            <div class="test-duration">Duration: {result["duration"]:.2f}s</div>
            <div class="test-output">
                <pre>{output_escaped[:5000]}</pre>
            </div>
        </div>
"""

    html += """
    </div>
</body>
</html>
"""

    report_path.write_text(html)


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Run E2E test suite")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--no-report", action="store_true", help="Skip HTML report generation")
    args = parser.parse_args()

    log_dir, report_dir = setup_directories()
    timestamp = get_timestamp()
    log_path = log_dir / f"e2e_test_{timestamp}.log"
    report_path = report_dir / f"e2e_test_{timestamp}.html"

    print(f"[{datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}] [INFO] Starting E2E test suite")
    print(f"[{datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}] [INFO] Log file: {log_path}")

    results = []
    overall_status = EXIT_SUCCESS

    for test_file in TEST_ORDER:
        test_path = Path(test_file)
        if not test_path.exists():
            print(f"[{datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}] [WARN] Test file not found: {test_file}")
            continue

        print(f"[{datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}] [INFO] Running: {test_file}")
        start_time = datetime.now()

        exit_code, stdout, stderr = run_test(test_file, args.verbose)
        duration = (datetime.now() - start_time).total_seconds()

        result = {
            "test_file": test_file,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "duration": duration,
        }
        results.append(result)

        status = "PASSED" if exit_code == 0 else "FAILED"
        print(f"[{datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}] [{status}] {test_file} ({duration:.2f}s)")

        if exit_code != 0:
            overall_status = EXIT_FAILURE

    print(f"[{datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}] [INFO] E2E test suite completed")

    if not args.no_report and results:
        generate_html_report(results, report_path)
        print(f"[{datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}] [INFO] HTML report: {report_path}")

    return overall_status


if __name__ == "__main__":
    sys.exit(main())
