"""Conftest for e2e scripts.

Excludes non-pytest test files from collection.
"""

# Exclude stress test from pytest collection - it's meant to be run standalone
collect_ignore = ["e2e_stress_test.py"]
