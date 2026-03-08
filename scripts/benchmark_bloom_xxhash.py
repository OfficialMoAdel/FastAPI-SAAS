#!/usr/bin/env python3
"""
Benchmark script for Bloom Filter xxhash migration.

Compares performance of SHA256 vs xxhash (xxh3_128) hash functions
for Bloom filter operations.

Usage:
    uv run python scripts/benchmark_bloom_xxhash.py

Expected Results:
    - xxhash should be 10-20x faster than SHA256
    - FPR should remain ~1% for both implementations
"""

import sys
import timeit
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from app.core.security.bloom_filter import BloomFilter


def benchmark_hash_function(use_xxhash: bool, iterations: int = 10000) -> dict:
    """Benchmark a specific hash function configuration.

    Args:
        use_xxhash: Whether to use xxhash (True) or SHA256 (False)
        iterations: Number of iterations for timing

    Returns:
        Dictionary with benchmark results
    """
    # Temporarily override settings for this benchmark
    from app.core.config import settings

    original_setting = settings.BLOOM_FILTER_USE_XXHASH
    settings.BLOOM_FILTER_USE_XXHASH = use_xxhash

    try:
        # Create bloom filter
        bf = BloomFilter(100_000, 0.01)

        # Sample token for testing
        token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"  # noqa: E501

        # Benchmark single hash operation
        single_hash_ns = (
            timeit.timeit(lambda: bf._hash_values(token), number=iterations)
            / iterations
            * 1e9
        )

        # Benchmark might_contain (common case)
        bf.add(token)
        contain_check_ns = (
            timeit.timeit(lambda: bf.might_contain(token), number=iterations)
            / iterations
            * 1e9
        )

        # Test with multiple tokens to verify FPR
        test_tokens = [f"token_{i}" for i in range(1000)]
        for t in test_tokens:
            bf.add(t)

        # Check false positive rate
        false_positives = 0
        test_count = 1000
        for i in range(test_count):
            test_token = f"nonexistent_{i}"
            if bf.might_contain(test_token):
                false_positives += 1

        actual_fpr = false_positives / test_count

        return {
            "hash_function": "xxhash (xxh3_128)" if use_xxhash else "SHA256",
            "single_hash_ns": round(single_hash_ns, 2),
            "might_contain_ns": round(contain_check_ns, 2),
            "actual_fpr": round(actual_fpr, 4),
            "target_fpr": 0.01,
            "hash_count": bf._hash_count,
            "size_bits": bf._size,
        }
    finally:
        # Restore original setting
        settings.BLOOM_FILTER_USE_XXHASH = original_setting


def main():
    """Run benchmarks and compare results."""
    print("=" * 70)
    print("Bloom Filter Hash Function Benchmark")
    print("=" * 70)
    print()

    # Load baseline metrics if available
    baseline_file = Path(__file__).parent.parent / "tests" / "perf_baseline_r1_opus.json"
    baseline_hash_ns = None
    baseline_contain_ns = None

    if baseline_file.exists():
        import json

        with open(baseline_file) as f:
            baseline = json.load(f)
            baseline_hash_ns = baseline.get("bloom_sha256_hash_ns")
            baseline_contain_ns = baseline.get("bloom_might_contain_ns")
            print(f"Baseline metrics loaded from: {baseline_file}")
            print(f"  - SHA256 hash: {baseline_hash_ns} ns")
            print(f"  - might_contain: {baseline_contain_ns} ns")
            print()

    # Benchmark SHA256 (legacy)
    print("Benchmarking SHA256 (legacy)...")
    sha256_results = benchmark_hash_function(use_xxhash=False)

    # Benchmark xxhash (new)
    print("Benchmarking xxhash (xxh3_128)...")
    xxhash_results = benchmark_hash_function(use_xxhash=True)

    # Display results
    print()
    print("=" * 70)
    print("RESULTS")
    print("=" * 70)
    print()

    print("SHA256 (Legacy):")
    print(f"  Single hash:     {sha256_results['single_hash_ns']:>10.2f} ns")
    print(f"  might_contain:   {sha256_results['might_contain_ns']:>10.2f} ns")
    print(f"  Actual FPR:      {sha256_results['actual_fpr']:>10.4f} (target: {sha256_results['target_fpr']})")
    print()

    print("xxhash (xxh3_128):")
    print(f"  Single hash:     {xxhash_results['single_hash_ns']:>10.2f} ns")
    print(f"  might_contain:   {xxhash_results['might_contain_ns']:>10.2f} ns")
    print(f"  Actual FPR:      {xxhash_results['actual_fpr']:>10.4f} (target: {xxhash_results['target_fpr']})")
    print()

    # Calculate speedup
    hash_speedup = sha256_results["single_hash_ns"] / xxhash_results["single_hash_ns"]
    contain_speedup = sha256_results["might_contain_ns"] / xxhash_results["might_contain_ns"]

    print("Performance Improvement:")
    print(f"  Single hash:     {hash_speedup:>10.2f}x faster")
    print(f"  might_contain:   {contain_speedup:>10.2f}x faster")
    print()

    # Compare with baseline if available
    if baseline_hash_ns and baseline_contain_ns:
        baseline_hash_speedup = baseline_hash_ns / xxhash_results["single_hash_ns"]
        baseline_contain_speedup = baseline_contain_ns / xxhash_results["might_contain_ns"]

        print("Improvement vs Baseline:")
        print(f"  Single hash:     {baseline_hash_speedup:>10.2f}x faster")
        print(f"  might_contain:   {baseline_contain_speedup:>10.2f}x faster")
        print()

    # Validation
    print("=" * 70)
    print("VALIDATION")
    print("=" * 70)
    print()

    success = True

    # Check speedup target (10x minimum)
    if hash_speedup < 10:
        print(f"WARNING: Hash speedup {hash_speedup:.2f}x is below 10x target")
        success = False
    else:
        print(f"PASS: Hash speedup {hash_speedup:.2f}x meets 10x target")

    # Check FPR consistency (within 50% of target)
    fpr_diff = abs(xxhash_results["actual_fpr"] - xxhash_results["target_fpr"])
    if fpr_diff > xxhash_results["target_fpr"] * 0.5:
        print(f"WARNING: xxhash FPR {xxhash_results['actual_fpr']:.4f} deviates from target {xxhash_results['target_fpr']}")  # noqa: E501
        success = False
    else:
        print(f"PASS: xxhash FPR {xxhash_results['actual_fpr']:.4f} is within acceptable range")

    print()
    if success:
        print("SUCCESS: All validation checks passed!")
        return 0
    else:
        print("FAILURE: Some validation checks failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
