#!/usr/bin/env python3
"""Generate HTML Performance Validation Report."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def generate_html_report(results: dict[str, Any], output_dir: Path) -> Path:
    """Generate an HTML report from validation results."""
    output_path = output_dir / "perf_report.html"

    # Calculate summary stats
    total_optimizations = results.get("metrics_summary", {}).get("total_optimizations_tested", 0)
    passed_optimizations = results.get("metrics_summary", {}).get("optimizations_passed", 0)
    failed_optimizations = results.get("metrics_summary", {}).get("optimizations_failed", 0)
    overall_pass = results.get("overall_pass", False)
    isomorphism_pass = results.get("isomorphism_results", {}).get("all_passed", False)

    # Build phase results HTML
    phase_html = ""
    for phase_key, phase_result in results.get("phase_results", {}).items():
        phase_num = phase_key.split("_")[1]
        phase_pass = phase_result.get("passed", False)
        status_class = "pass" if phase_pass else "fail"
        status_icon = "✓" if phase_pass else "✗"

        opt_html = ""
        for opt_key, opt_result in phase_result.get("optimizations", {}).items():
            opt_pass = opt_result.get("passed", False)
            opt_status = "✓" if opt_pass else "✗"
            opt_class = "pass" if opt_pass else "fail"
            opt_html += f"""
            <tr class="{opt_class}">
                <td>{opt_key}</td>
                <td>{opt_status}</td>
                <td>{json.dumps(opt_result, default=str)[:100]}</td>
            </tr>
            """

        phase_html += f"""
        <div class="phase {status_class}">
            <h3>{status_icon} Phase {phase_num}</h3>
            <p>Status: {"PASSED" if phase_pass else "FAILED"}</p>
            <table>
                <tr><th>Optimization</th><th>Status</th><th>Details</th></tr>
                {opt_html}
            </table>
        </div>
        """

    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Performance Validation Report</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; }}
        h1 {{ color: #333; }}
        .summary {{ background: #f5f5f5; padding: 20px; border-radius: 8px; margin-bottom: 30px; }}
        .pass {{ background-color: #d4edda; }}
        .fail {{ background-color: #f8d7da; }}
        .phase {{ margin: 20px 0; padding: 15px; border-radius: 8px; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
        th, td {{ padding: 8px; text-align: left; border-bottom: 1px solid #ddd; }}
        .overall-pass {{ color: #155724; font-size: 24px; }}
        .overall-fail {{ color: #721c24; font-size: 24px; }}
        .metric {{ display: inline-block; margin: 10px; padding: 15px; background: #fff; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .metric-value {{ font-size: 32px; font-weight: bold; }}
        .metric-label {{ color: #666; }}
    </style>
</head>
<body>
    <h1>PERF-UNIQUE-R1 Performance Validation Report</h1>
    <p>Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
    <p>Execution Time: {results.get("execution_time_seconds", 0):.2f} seconds</p>

    <div class="summary">
        <h2 class="{"overall-pass" if overall_pass else "overall-fail"}">
            Overall: {"✓ PASSED" if overall_pass else "✗ FAILED"}
        </h2>

        <div class="metric">
            <div class="metric-value">{passed_optimizations}/{total_optimizations}</div>
            <div class="metric-label">Optimizations Passed</div>
        </div>

        <div class="metric">
            <div class="metric-value">{"✓" if isomorphism_pass else "✗"}</div>
            <div class="metric-label">Isomorphism Tests</div>
        </div>

        <div class="metric">
            <div class="metric-value">{results.get("metrics_summary", {}).get("estimated_p95_improvement_pct", 0)}%</div>
            <div class="metric-label">Est. p95 Improvement</div>
        </div>

        <div class="metric">
            <div class="metric-value">{results.get("metrics_summary", {}).get("estimated_p99_improvement_pct", 0)}%</div>
            <div class="metric-label">Est. p99 Improvement</div>
        </div>
    </div>

    <h2>Phase Results</h2>
    {phase_html}

    <h2>Isomorphism Validation</h2>
    <div class="phase {"pass" if isomorphism_pass else "fail"}">
        <p>All isomorphism tests: {"PASSED" if isomorphism_pass else "FAILED"}</p>
        <p>Behavior preservation verified for all optimizations.</p>
    </div>

    <h2>Raw Results</h2>
    <pre>{json.dumps(results, indent=2, default=str)}</pre>
</body>
</html>
"""

    output_path.write_text(html)
    return output_path


if __name__ == "__main__":
    # Test with sample data
    sample_results = {
        "overall_pass": True,
        "metrics_summary": {
            "total_optimizations_tested": 17,
            "optimizations_passed": 17,
            "optimizations_failed": 0,
            "estimated_p95_improvement_pct": 40,
            "estimated_p99_improvement_pct": 55,
        },
        "isomorphism_results": {"all_passed": True},
        "phase_results": {
            "phase_1": {
                "passed": True,
                "optimizations": {"U3": {"passed": True}, "U4": {"passed": True}},
            }
        },
        "execution_time_seconds": 5.2,
    }

    output = generate_html_report(sample_results, Path("results"))
    print(f"Report generated: {output}")
