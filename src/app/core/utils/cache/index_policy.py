"""Cache-index lifetime policy.

Index entries are metadata about data keys, so they must outlive the data they
describe. If an index expires at or before its data key, a later write can find
an empty index and miss still-live keys (stale reads). Dead index members are
harmless: deleting an expired key is a no-op.

One helper keeps that rule from being restated (and drifting) across the cache
submodules.
"""


def index_ttl(data_ttl: int) -> int:
    """Return the TTL for an index set that describes a data key of ``data_ttl``."""
    return data_ttl * 2
