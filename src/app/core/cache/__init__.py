"""Core cache infrastructure: client, dependencies, invalidation channel, warmer.

Package init is required for grimp/import-linter visibility — without it this
directory is an implicit namespace package and the H-5 forbidden-contract
cannot see its imports (blind spot in the CI layering gate).
"""
