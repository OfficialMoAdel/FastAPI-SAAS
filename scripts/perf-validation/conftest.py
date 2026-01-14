"""Exclude Locust load test files from pytest collection.

Locust uses gevent's monkey-patching which conflicts with pytest's
assertion rewriting, causing RecursionError on import.
"""

# Files to ignore during pytest collection
collect_ignore = ["load_test.py"]
