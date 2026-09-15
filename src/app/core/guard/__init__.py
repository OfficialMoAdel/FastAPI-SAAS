"""Request-guard seams: IP rules + middleware ordering.

Ordering is caller knowledge no more: ``GUARD_ORDER`` (innermost →
outermost registration) is the single wiring point. ``main.py`` iterates
it; the R1-16 contract test pins it. Exempt paths live in
``core/middleware_exempt.py`` (unchanged); IP ban writes go through
:class:`IpRules` (layering fix).
"""

from src.app.core.guard.ip_rules import IpRules, RedisIpRules
from src.app.core.guard.ordering import GUARD_ORDER, resolve_guard_order

__all__ = ["GUARD_ORDER", "IpRules", "RedisIpRules", "resolve_guard_order"]
