"""
Lua Script Manager for R1-UNIFIED Optimizations.

Provides centralized management of Redis Lua scripts with:
- Script registration with SHA1 tracking
- Preloading on startup (SCRIPT LOAD)
- Automatic EVALSHA with NOSCRIPT fallback
- Error handling and logging

Usage:
    lua_manager = get_lua_manager()
    await lua_manager.preload_scripts(redis_client)
    result = await lua_manager.execute(
        redis_client,
        "idempotency_check",
        keys=["key1"],
        args=["arg1", "arg2"]
    )
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from redis.exceptions import NoScriptError

from src.app.core.logger import get_logger
from src.app.core.utils.retry import LuaScriptError

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = get_logger(__name__)


class ScriptNotFoundError(LuaScriptError):
    """Script not registered in manager."""

    pass


@dataclass
class LuaScript:
    """Registered Lua script with metadata."""

    name: str
    source: str
    sha1: str = field(default="", init=False)
    loaded: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        """Calculate SHA1 hash of script source."""
        self.sha1 = hashlib.sha1(self.source.encode(), usedforsecurity=False).hexdigest()


# GCRA theoretical-arrival-time core. Redis Lua scripts are self-contained --
# there is no include/require in the Redis sandbox, so the shared arithmetic is
# composed Python-side into each composite script rather than copy-pasted into
# three of them. Edit the arithmetic once, all three stay in step.
_GCRA_TAT_FRAGMENT = """
            local tat_ms = tonumber(redis.call('GET', __GCRA_KEY__)) or now_ms
            local new_tat_ms = math.max(now_ms, tat_ms) + emission_interval_ms
            local allow_at_ms = new_tat_ms - burst_tolerance_ms

            if allow_at_ms > now_ms then
                local retry_after_ms = allow_at_ms - now_ms
                return __LIMITED__
            else
                redis.call('SET', __GCRA_KEY__, new_tat_ms)
                redis.call('PEXPIRE', __GCRA_KEY__, math.ceil(period_ms + emission_interval_ms))
                local remaining = 0
                if emission_interval_ms > 0 then
                    remaining = math.floor((now_ms + burst_tolerance_ms - new_tat_ms) / emission_interval_ms)
                end
                remaining = math.max(0, math.min(remaining, limit - 1))
                return __ALLOWED__
            end
"""


def _gcra_tat(*, key: str, limited: str, allowed: str) -> str:
    """Compose the shared GCRA core for one script's return shape."""
    return (
        _GCRA_TAT_FRAGMENT
        .replace("__GCRA_KEY__", key)
        .replace("__LIMITED__", limited)
        .replace("__ALLOWED__", allowed)
    )


class LuaScriptManager:
    """Manages Redis Lua scripts with preloading and automatic fallback.

    This manager provides:
    1. Script registration with automatic SHA1 calculation
    2. Batch preloading on startup (SCRIPT LOAD)
    3. EVALSHA execution with automatic NOSCRIPT fallback
    4. Centralized error handling and logging
    """

    # Pre-defined scripts for R1-UNIFIED optimizations
    BUILTIN_SCRIPTS = {
        # P1-1: Event Bus Idempotency - Atomic check-and-set
        "idempotency_check": """
            -- KEYS[1]: idempotency key
            -- ARGV[1]: TTL in seconds
            -- ARGV[2]: value (usually "1")
            -- Returns: 1 if key was set (first time), 0 if already exists
            local result = redis.call('SETNX', KEYS[1], ARGV[2])
            if result == 1 then
                redis.call('EXPIRE', KEYS[1], ARGV[1])
            end
            return result
        """,
        # P2-2: XFetch - Atomic GET + TTL in single roundtrip
        "xfetch_get_with_ttl": """
            -- KEYS[1]: cache key
            -- Returns: [value, ttl] or [nil, -2] if not exists
            local value = redis.call('GET', KEYS[1])
            if value == false then
                return {'', -2}
            end
            local ttl = redis.call('TTL', KEYS[1])
            return {value, ttl}
        """,
        # Cache stampede single-flight: atomic "read, or become the one caller
        # allowed to fetch" in a single roundtrip. Claiming the lock inside the
        # same script as the read is what makes it single-flight: a caller that
        # read-and-then-locked in Python can be interleaved between its GET and
        # its SET NX, so N concurrent misses all read empty and all fetch.
        # ARGV[1] = lock TTL seconds, ARGV[2] = ownership token.
        # Returns: [value, ttl, claimed] where claimed=1 means "you own the
        # refresh, go fetch and publish"; claimed=0 means "someone else owns
        # it, wait and re-read".
        "xfetch_get_or_claim": """
            local value = redis.call('GET', KEYS[1])
            if value then
                return {value, redis.call('TTL', KEYS[1]), 0}
            end
            local set = redis.call('SET', KEYS[2], ARGV[2], 'NX', 'EX', tonumber(ARGV[1]))
            if not set then
                return {'', -2, 0}
            end
            return {'', -2, 1}
        """,
        # P3-1: DLQ Bounded Size - LPUSH with LTRIM
        "dlq_bounded_push": """
            -- KEYS[1]: DLQ list key
            -- ARGV[1]: max size
            -- ARGV[2]: JSON value to push
            -- Returns: new length after trim
            redis.call('LPUSH', KEYS[1], ARGV[2])
            redis.call('LTRIM', KEYS[1], 0, tonumber(ARGV[1]) - 1)
            return redis.call('LLEN', KEYS[1])
        """,
        # P5: RBAC Permission Cache - Get with metadata
        "rbac_cache_get": """
            -- KEYS[1]: permissions hash key
            -- ARGV[1]: user_id field
            -- Returns: [value, ttl] or [nil, -2]
            local value = redis.call('HGET', KEYS[1], ARGV[1])
            if value == false then
                return {false, -2}
            end
            local ttl = redis.call('TTL', KEYS[1])
            return {value, ttl}
        """,
        # Distributed lock release - compare-and-delete (ownership token)
        "lock_release_if_owned": """
            -- KEYS[1]: lock key
            -- ARGV[1]: ownership token
            -- Returns: 1 if lock was released, 0 if not owned
            if redis.call('GET', KEYS[1]) == ARGV[1] then
                return redis.call('DEL', KEYS[1])
            end
            return 0
        """,
        # MFA pending-token single-flight claim. The backup-code redemption
        # mutates and commits, so exactly one request may be inside the
        # factor verify at a time or a loser still burns the victim's codes.
        # SET NX is the arbitration: Redis runs scripts one at a time, so
        # the winner is decided by a single atomic step and every loser is
        # refused before it can touch state. The claim is separate from the
        # token key itself so it can be released on a failed verify (a typo
        # must not cost the user their pending challenge).
        # KEYS[1]: claim key
        # ARGV[1]: claim TTL seconds, ARGV[2]: ownership nonce
        # Returns: 1 if claimed (you own the verify), 0 if someone else does.
        "mfa_pending_claim": """
            local set = redis.call('SET', KEYS[1], ARGV[2], 'NX', 'EX', tonumber(ARGV[1]))
            if not set then
                return 0
            end
            return 1
        """,
        # Fixed-window rate limit: atomic INCR with window-aligned TTL
        # (migrated from utils/rate_limit.RATE_LIMIT_LUA_SCRIPT)
        "rate_limit_fixed_window": """
            -- KEYS[1]: rate limit counter key PREFIX
            -- ARGV[1]: window period in seconds
            -- Returns: current count after increment
            -- R5 FIX: the window boundary is derived from the Redis clock
            -- (redis TIME) instead of the app clock, so app/Redis clock skew
            -- can no longer create a window key whose TTL belongs to a
            -- different window.
            local period = tonumber(ARGV[1])
            local now = redis.call('TIME')[1]
            local window_start = now - (now % period)
            local key = KEYS[1] .. ':' .. tostring(window_start)
            local current = redis.call('INCR', key)
            if current == 1 then
                redis.call('EXPIRE', key, period - (now % period))
            end
            return current
        """,
        # GCRA rate limit: atomic TAT update (migrated from gcra_rate_limit)
        "rate_limit_gcra": """
            -- KEYS[1]: GCRA TAT key
            -- ARGV[1]: now_ms, ARGV[2]: emission_interval_ms,
            -- ARGV[3]: limit, ARGV[4]: burst_tolerance_ms, ARGV[5]: period_ms
            -- Returns: [is_allowed(0/1), remaining, retry_after_ms, reset_at_ms]
            local key = KEYS[1]
            local now_ms = tonumber(ARGV[1])
            local emission_interval_ms = tonumber(ARGV[2])
            local limit = tonumber(ARGV[3])
            local period_ms = tonumber(ARGV[5])

            local burst_tolerance_ms = tonumber(ARGV[4])
            """ + _gcra_tat(
                key="key",
                limited="{0, 0, retry_after_ms, new_tat_ms}",
                allowed="{1, remaining, 0, new_tat_ms}",
            ) + """
        """,
        # Token blacklist check: atomic 4-op sequence
        # (migrated from token_blacklist_cache.BLACKLIST_CHECK_LUA_SCRIPT)
        "token_blacklist_check": """
            -- KEYS[1]: negative cache key, KEYS[2]: blacklist zset key
            -- ARGV[1]: member (jti:{jti} or token), ARGV[2]: now, ARGV[3]: neg-cache TTL
            if redis.call('EXISTS', KEYS[1]) == 1 then
                return 0
            end
            local score = redis.call('ZSCORE', KEYS[2], ARGV[1])
            if not score then
                redis.call('SETEX', KEYS[1], ARGV[3], '1')
                return 0
            end
            if tonumber(score) < tonumber(ARGV[2]) then
                redis.call('ZREM', KEYS[2], ARGV[1])
                redis.call('SETEX', KEYS[1], ARGV[3], '1')
                return 0
            end
            return 1
        """,
        # S-04: compare-and-delete for the webhook in-flight lock.
        # A blind DEL let a slow, later-failing delivery erase the terminal
        # "processed" marker a faster, already-successful delivery had written
        # to the SAME key, which re-opened double processing.
        "compare_and_delete": """
            -- KEYS[1]: the lock / marker key
            -- ARGV[1]: the lock token this caller owns
            -- Returns: 1 if deleted (we owned it), 0 if absent or owned by
            --          someone else (their value is left untouched).
            if redis.call('GET', KEYS[1]) == ARGV[1] then
                return redis.call('DEL', KEYS[1])
            end
            return 0
        """,
        # Session registration + max-session eviction
        # (migrated from session_manager register_session inline script)
        "session_register_with_eviction": """
            -- KEYS[1]: order zset, KEYS[2]: session hash
            -- ARGV[1]: max_sessions, ARGV[2]: session_id, ARGV[3]: session_data,
            -- ARGV[4]: timestamp, ARGV[5]: ttl, ARGV[6]: max_batch
            local zset_key = KEYS[1]
            local hash_key = KEYS[2]
            local max_sessions = tonumber(ARGV[1])
            local session_id = ARGV[2]
            local session_data = ARGV[3]
            local timestamp = ARGV[4]
            local ttl = tonumber(ARGV[5])
            local max_batch = tonumber(ARGV[6])

            if redis.call('HEXISTS', hash_key, session_id) == 1 or redis.call('ZSCORE', zset_key, session_id) then
                -- A9 FIX: re-registration of an existing session must still
                -- refresh the key TTLs; returning early without EXPIRE let
                -- actively-used session keys lapse.
                redis.call('EXPIRE', zset_key, ttl)
                redis.call('EXPIRE', hash_key, ttl)
                return {"already_exists"}
            end

            local current_count = redis.call('ZCARD', zset_key)
            if current_count == 0 then
                current_count = redis.call('HLEN', hash_key)
            end

            local evicted_data = {}
            local final_count = current_count + 1

            -- A9 FIX: eviction was previously capped at a single max_batch
            -- slice per call, so an over-cap registry remained over cap after
            -- every registration. Evict repeatedly in bounded batches until
            -- there is room for the new session; each iteration removes at
            -- least one entry or breaks, so the loop always terminates.
            while current_count >= max_sessions do
                local sessions_to_remove = current_count - max_sessions + 1
                if sessions_to_remove > max_batch then
                    sessions_to_remove = max_batch
                end
                local session_ids = redis.call('ZRANGE', zset_key, 0, sessions_to_remove - 1)
                if #session_ids == 0 then break end
                for i, sid in ipairs(session_ids) do
                    local data = redis.call('HGET', hash_key, sid)
                    if data then
                        table.insert(evicted_data, data)
                    end
                    redis.call('HDEL', hash_key, sid)
                    redis.call('ZREM', zset_key, sid)
                end
                current_count = current_count - #session_ids
                final_count = current_count + 1
            end

            redis.call('ZADD', zset_key, timestamp, session_id)
            redis.call('HSET', hash_key, session_id, session_data)
            redis.call('EXPIRE', zset_key, ttl)
            redis.call('EXPIRE', hash_key, ttl)

            local result = {"success", tostring(final_count)}
            for _, d in ipairs(evicted_data) do
                table.insert(result, d)
            end
            return result
        """,
        # Session invalidate: check-exists + HDEL + ZREM in a single atomic
        # operation so commandstats on Redis sees ONE EVALSHA per invalidate
        # call (S-1, dxra-adjacent). Returns 1 if the session existed and was
        # removed, 0 when it was absent.
        "session_invalidate": """
            -- KEYS[1]: session hash key, KEYS[2]: session order zset key
            -- ARGV[1]: session_id
            if redis.call('HEXISTS', KEYS[1], ARGV[1]) == 0 then
                return 0
            end
            redis.call('HDEL', KEYS[1], ARGV[1])
            redis.call('ZREM', KEYS[2], ARGV[1])
            return 1
        """,
        # MFA lockout: atomic check + increment
        # (migrated from mfa/service._MFA_CHECK_LUA)
        "mfa_lockout_check": """
            -- KEYS[1]: attempts key, ARGV[1]: max_attempts, ARGV[2]: lockout_duration
            -- Returns: -1 if locked out, otherwise new attempt count
            local key = KEYS[1]
            local max_attempts = tonumber(ARGV[1])
            local lockout_duration = tonumber(ARGV[2])
            local current = redis.call('GET', key)
            if current and tonumber(current) >= max_attempts then
                local ttl = redis.call('TTL', key)
                if ttl > 0 then
                    return -1
                end
            end
            local new_count = redis.call('INCR', key)
            redis.call('EXPIRE', key, lockout_duration)
            return new_count
        """,
        # Atomic GETDEL (folded from utils/redis_utils._GETDEL_LUA)
        "get_delete": """
            -- KEYS[1]: key
            -- Returns: value before deletion, or nil
            local val = redis.call('GET', KEYS[1])
            if val then
                redis.call('DEL', KEYS[1])
            end
            return val
        """,
        # P1-1: Composite Auth Prefetch (Blacklist + Session + RBAC Bitmask in 1 RTT)
        "composite_auth_prefetch": """
            -- KEYS[1]: negative blacklist cache key (e.g. token:verified:{hash})
            -- KEYS[2]: blacklist zset key (e.g. token:blacklist)
            -- KEYS[3]: session hash key (e.g. session:data:{user_id})
            -- KEYS[4]: rbac bitmask key (e.g. perms:bitmask:{tenant}:{user}:{version})
            -- ARGV[1]: blacklist member (e.g. jti:{jti} or token)
            -- ARGV[2]: now (epoch timestamp in seconds)
            -- ARGV[3]: negative cache TTL (seconds)
            -- ARGV[4]: session_id (e.g. sid or jti)
            -- ARGV[5]: check_bitmask flag ("1" or "0")
            -- Returns: {err (0/1), is_blacklisted (0/1/-1), session_valid (0/1/-1), bitmask (string/false)}

            local err = 0
            local function safe_call(...)
                local ok, res = pcall(redis.call, ...)
                if not ok then
                    err = 1
                    return nil
                end
                return res
            end

            local is_blacklisted = -1
            if ARGV[1] ~= '' and KEYS[2] ~= '' then
                local exists_neg = (KEYS[1] ~= '') and safe_call('EXISTS', KEYS[1])
                if exists_neg == 1 then
                    is_blacklisted = 0
                elseif err == 0 then
                    local score = safe_call('ZSCORE', KEYS[2], ARGV[1])
                    if err == 0 then
                        if not score then
                            if KEYS[1] ~= '' then
                                safe_call('SETEX', KEYS[1], ARGV[3], '1')
                            end
                            is_blacklisted = 0
                        elseif tonumber(score) < tonumber(ARGV[2]) then
                            safe_call('ZREM', KEYS[2], ARGV[1])
                            if KEYS[1] ~= '' then
                                safe_call('SETEX', KEYS[1], ARGV[3], '1')
                            end
                            is_blacklisted = 0
                        else
                            is_blacklisted = 1
                        end
                    end
                end
            end

            local session_valid = -1
            if ARGV[4] ~= '' and KEYS[3] ~= '' and err == 0 then
                local s_res = safe_call('HEXISTS', KEYS[3], ARGV[4])
                if err == 0 and s_res ~= nil then
                    session_valid = s_res
                end
            end

            local bitmask = ''
            if ARGV[5] == '1' and KEYS[4] ~= '' and err == 0 then
                local b_res = safe_call('GET', KEYS[4])
                if err == 0 and b_res ~= nil and b_res ~= false then
                    bitmask = b_res
                end
            end

            return {err, is_blacklisted, session_valid, bitmask}
        """,
        # P3-1: Auto-Ban IP (Atomic SETNX + INCR + EXPIRE + DEL in 1 RTT)
        "auto_ban_ip": """
            -- KEYS[1]: ban_key (rate_limit:ban:{ip})
            -- KEYS[2]: count_key (rate_limit:ban_count:{ip})
            -- KEYS[3]: violations_key (rate_limit:violations:{ip})
            -- ARGV[1]: reason
            -- ARGV[2]: ban_duration (seconds)
            -- ARGV[3]: count_ttl (seconds)
            -- Returns: {is_new_ban (0/1), new_ban_count}

            local set_res = redis.call('SET', KEYS[1], ARGV[1], 'EX', tonumber(ARGV[2]), 'NX')
            if not set_res then
                return {0, 0}
            end

            local ban_count = redis.call('INCR', KEYS[2])
            if ban_count == 1 then
                redis.call('EXPIRE', KEYS[2], tonumber(ARGV[3]))
            end

            if KEYS[3] and KEYS[3] ~= '' then
                redis.call('DEL', KEYS[3])
            end

            return {1, ban_count}
        """,
        # H-3: Ban check + GCRA in ONE round-trip (middleware hot path).
        # Combines "rate_limit_gcra" TAT logic with the ban-key GET + TTL so
        # the middleware makes 1 Redis RTT instead of 2 for banned-or-limited.
        "ban_and_gcra_check": """
            -- KEYS[1]: GCRA TAT key (e.g. gcra:tenant:{tid}:{uid}:{path})
            -- KEYS[2]: ban key (e.g. security:ban:{ip})
            -- ARGV[1]: now_ms, ARGV[2]: emission_interval_ms, ARGV[3]: limit,
            -- ARGV[4]: burst_tolerance_ms, ARGV[5]: period_ms, ARGV[6]: check_gcra ("1"/"0")
            -- Returns: {is_allowed, remaining, retry_after_ms, is_banned, ban_reason, ban_ttl}
            local ban_key = KEYS[2]
            local ban_reason = redis.call('GET', ban_key)
            local is_banned = 0
            local ban_ttl = 0
            if ban_reason then
                is_banned = 1
                ban_ttl = redis.call('TTL', ban_key)
            end

            if is_banned == 1 or ARGV[6] == '0' then
                -- Rejected by ban (skip GCRA — the request dies here), or GCRA
                -- not requested: return neutral GCRA values with ban status.
                return {0, 0, 0, is_banned, ban_reason or '', ban_ttl}
            end

            local now_ms = tonumber(ARGV[1])
            local emission_interval_ms = tonumber(ARGV[2])
            local limit = tonumber(ARGV[3])
            local period_ms = tonumber(ARGV[5])
            local burst_tolerance_ms = tonumber(ARGV[4])
            local key = KEYS[1]
            """ + _gcra_tat(
                key="key",
                limited="{0, 0, retry_after_ms, 0, '', 0}",
                allowed="{1, remaining, 0, 0, '', 0}",
            ) + """
        """,
        # P0-2: Blacklist + ban check + GCRA in ONE round-trip (authed hot path).
        # Merges the middleware's sequential is_token_blacklisted Lua probe +
        # ban_and_gcra_check script (2 RTTs) into a single script (1 RTT).
        # The GCRA key depends on the in-script blacklist verdict, so the
        # caller passes BOTH candidate keys/limits and the script consumes
        # exactly one of them. Blacklist/ban/GCRA sections mirror the
        # standalone token_blacklist_check and ban_and_gcra_check scripts.
        "ban_blacklist_gcra_check": """
            -- KEYS[1]: negative blacklist cache key (e.g. token:verified:{hash})
            -- KEYS[2]: blacklist zset key (e.g. token:blacklist)
            -- KEYS[3]: authed GCRA TAT key (e.g. gcra:rate_limit:user:{uid}:{path})
            -- KEYS[4]: ban key (e.g. security:ban:{ip})
            -- KEYS[5]: anonymous GCRA TAT key (e.g. gcra:rate_limit:anonymous:ip:{ip}:{path})
            -- ARGV[1]: blacklist member (jti:{jti} or token), ARGV[2]: now (sec),
            -- ARGV[3]: negative-cache TTL (sec), ARGV[4]: now_ms,
            -- ARGV[5..8]: authed emission_interval_ms, limit, burst_tolerance_ms, period_ms
            -- ARGV[9..12]: anonymous emission_interval_ms, limit, burst_tolerance_ms, period_ms
            -- Returns: {is_blacklisted, is_allowed, remaining, retry_after_ms, is_banned, ban_reason, ban_ttl}
            local is_blacklisted = 0
            if redis.call('EXISTS', KEYS[1]) == 0 then
                local score = redis.call('ZSCORE', KEYS[2], ARGV[1])
                if not score then
                    redis.call('SETEX', KEYS[1], ARGV[3], '1')
                elseif tonumber(score) < tonumber(ARGV[2]) then
                    redis.call('ZREM', KEYS[2], ARGV[1])
                    redis.call('SETEX', KEYS[1], ARGV[3], '1')
                else
                    is_blacklisted = 1
                end
            end

            local ban_reason = redis.call('GET', KEYS[4])
            local is_banned = 0
            local ban_ttl = 0
            if ban_reason then
                is_banned = 1
                ban_ttl = redis.call('TTL', KEYS[4])
            end

            if is_banned == 1 then
                return {is_blacklisted, 0, 0, 0, 1, ban_reason, ban_ttl}
            end

            local base = 5
            local gcra_key = KEYS[3]
            if is_blacklisted == 1 then
                base = 9
                gcra_key = KEYS[5]
            end
            local emission_interval_ms = tonumber(ARGV[base])
            local limit = tonumber(ARGV[base + 1])
            local burst_tolerance_ms = tonumber(ARGV[base + 2])
            local period_ms = tonumber(ARGV[base + 3])
            local now_ms = tonumber(ARGV[4])
            """ + _gcra_tat(
                key="gcra_key",
                limited="{is_blacklisted, 0, 0, retry_after_ms, 0, '', 0}",
                allowed="{is_blacklisted, 1, remaining, 0, 0, '', 0}",
            ) + """
        """,
        # P2: DLQ source-filtered pop - server-side oldest-first scan + LREM.
        "dlq_pop_filtered": """
            -- KEYS[1]: DLQ list key (tail = oldest; RPUSH adds at head)
            -- ARGV[1]: wanted source ("event_bus" or "arq"; missing field counts as "arq")
            -- Returns: matched serialized element, or false when none matches.
            -- Mirror of DeadLetterQueue._retry_scan: only JSON objects
            -- participate (raw must start with '{'), unparseable entries are
            -- skipped, LREM count=1 removes the exact serialized element.
            local entries = redis.call('LRANGE', KEYS[1], 0, -1)
            for i = #entries, 1, -1 do
                local raw = entries[i]
                if string.match(raw, '^%s*{') then
                    local ok, obj = pcall(cjson.decode, raw)
                    if ok and type(obj) == 'table' then
                        local src = obj['source']
                        if src == nil then src = 'arq' end
                        if src == ARGV[1] then
                            redis.call('LREM', KEYS[1], 1, raw)
                            return raw
                        end
                    end
                end
            end
            return false
        """,
        # P2: DLQ source-filtered clear - server-side scan + per-match LREM.
        "dlq_clear_filtered": """
            -- KEYS[1]: DLQ list key
            -- ARGV[1]: wanted source ("event_bus" or "arq"; missing field counts as "arq")
            -- Returns: number of entries removed.
            -- Mirror of DeadLetterQueue._clear_scan: head-to-tail order, one
            -- LREM count=1 per match, count incremented per match.
            local entries = redis.call('LRANGE', KEYS[1], 0, -1)
            local cleared = 0
            for i = 1, #entries do
                local raw = entries[i]
                if string.match(raw, '^%s*{') then
                    local ok, obj = pcall(cjson.decode, raw)
                    if ok and type(obj) == 'table' then
                        local src = obj['source']
                        if src == nil then src = 'arq' end
                        if src == ARGV[1] then
                            redis.call('LREM', KEYS[1], 1, raw)
                            cleared = cleared + 1
                        end
                    end
                end
            end
            return cleared
        """,
    }

    def __init__(self) -> None:
        """Initialize the Lua script manager."""
        self._scripts: dict[str, LuaScript] = {}
        self._preloaded: bool = False

        # Register built-in scripts
        for name, source in self.BUILTIN_SCRIPTS.items():
            self.register(name, source)

    def register(self, name: str, source: str) -> LuaScript:
        """Register a Lua script.

        Args:
            name: Unique script identifier
            source: Lua script source code

        Returns:
            LuaScript: Registered script object
        """
        # Normalize whitespace for consistent SHA1
        normalized_source = "\n".join(line.strip() for line in source.strip().splitlines())
        script = LuaScript(name=name, source=normalized_source)
        self._scripts[name] = script
        logger.debug("Registered Lua script", name=name, sha1=script.sha1[:8])
        return script

    def get_script(self, name: str) -> LuaScript:
        """Get a registered script by name.

        Args:
            name: Script identifier

        Returns:
            LuaScript: The registered script

        Raises:
            ScriptNotFoundError: If script is not registered
        """
        if name not in self._scripts:
            raise ScriptNotFoundError(f"Script '{name}' not registered")
        return self._scripts[name]

    async def preload_scripts(self, redis: Redis) -> dict[str, bool]:
        """Preload all registered scripts into Redis.

        This uses SCRIPT LOAD to cache scripts server-side,
        enabling faster EVALSHA execution.

        Args:
            redis: Redis client

        Returns:
            dict[str, bool]: Script name -> success status
        """
        import asyncio

        results: dict[str, bool] = {}

        async def _load_one(name: str, script: LuaScript) -> tuple[str, bool]:
            try:
                # SCRIPT LOAD returns the server's SHA1 hash
                loaded_sha = await redis.script_load(script.source)

                # Verify SHA1 matches. A mismatch usually means the mock/test
                # double returned a fake SHA — never overwrite the local hash,
                # because execute() sends `script.source` verbatim in its EVAL
                # fallback and Redis computes the same digest locally.
                if loaded_sha != script.sha1:
                    logger.warning(
                        "Script SHA1 mismatch; keeping locally computed SHA",
                        name=name,
                        expected=script.sha1[:8],
                        got=str(loaded_sha)[:8],
                    )

                script.loaded = True
                logger.debug("Preloaded script", name=name)
                return name, True

            except Exception as e:
                logger.error("Failed to preload script", name=name, error=e)
                return name, False

        # K10: concurrent SCRIPT LOAD — one startup latency, not N sequential RTTs.
        outcomes = await asyncio.gather(*(_load_one(name, script) for name, script in self._scripts.items()))
        results = dict(outcomes)

        self._preloaded = True
        loaded_count = sum(results.values())
        logger.info("Preloaded / Lua scripts", loaded_count=loaded_count, val=len(self._scripts))

        return results

    async def execute(
        self,
        redis: Redis,
        script_name: str,
        keys: list[str] | None = None,
        args: list[str] | None = None,
    ) -> Any:
        """Execute a registered Lua script.

        Uses EVALSHA for efficiency, with automatic NOSCRIPT fallback.

        Args:
            redis: Redis client
            script_name: Name of registered script
            keys: KEYS arguments for the script
            args: ARGV arguments for the script

        Returns:
            Script execution result

        Raises:
            ScriptNotFoundError: If script is not registered
            LuaScriptError: If script execution fails
        """
        script = self.get_script(script_name)
        keys = keys or []
        args = args or []

        try:
            # Try EVALSHA first (faster if script is cached)
            result = await redis.evalsha(  # type: ignore[misc]
                script.sha1,
                len(keys),
                *keys,
                *args,
            )
            script.loaded = True
            return result

        except Exception as e:
            # redis-py strips the "NOSCRIPT" error code prefix, raising
            # NoScriptError("No matching script. Please use EVAL.") — so match
            # both the exception type and the message text (covers mocks).
            is_noscript = (
                isinstance(e, NoScriptError) or "NOSCRIPT" in str(e).upper() or "NO MATCHING SCRIPT" in str(e).upper()
            )

            # Handle NOSCRIPT error - script not in Redis cache; reload via SCRIPT LOAD
            if is_noscript:
                logger.debug("Script not cached in Redis, reloading via SCRIPT LOAD", script_name=script_name)
                try:
                    loaded_sha = await redis.script_load(script.source)  # type: ignore[misc]
                    if loaded_sha != script.sha1:
                        logger.warning(
                            "Script SHA1 mismatch on reload; keeping locally computed SHA",
                            name=script_name,
                            expected=script.sha1[:8],
                            got=str(loaded_sha)[:8],
                        )
                    script.loaded = True
                    return await redis.evalsha(  # type: ignore[misc]
                        script.sha1,
                        len(keys),
                        *keys,
                        *args,
                    )
                except Exception as load_err:
                    logger.warning(
                        "SCRIPT LOAD / EVALSHA reload failed, falling back to EVAL",
                        script_name=script_name,
                        error=str(load_err),
                    )
                    try:
                        result = await redis.eval(  # type: ignore[misc]
                            script.source,
                            len(keys),
                            *keys,
                            *args,
                        )
                        script.loaded = False
                        return result
                    except Exception as eval_error:
                        raise LuaScriptError(f"EVAL fallback failed for '{script_name}': {eval_error}") from eval_error
            # Other Redis errors
            raise LuaScriptError(f"Script '{script_name}' execution failed: {e}") from e

    async def execute_or_fallback(
        self,
        redis: Redis,
        script_name: str,
        keys: list[str] | None = None,
        args: list[str] | None = None,
        fallback_fn: Any | None = None,
    ) -> Any:
        """Execute script with optional Python fallback.

        Args:
            redis: Redis client
            script_name: Name of registered script
            keys: KEYS arguments
            args: ARGV arguments
            fallback_fn: Async function to call if script fails

        Returns:
            Script result or fallback result
        """
        try:
            return await self.execute(redis, script_name, keys, args)
        except LuaScriptError as e:
            if fallback_fn is not None:
                logger.warning("Script failed, using fallback", script_name=script_name, error=e)
                return await fallback_fn()
            raise

    def is_preloaded(self) -> bool:
        """Check if scripts have been preloaded."""
        return self._preloaded

    def get_script_names(self) -> list[str]:
        """Get list of registered script names."""
        return list(self._scripts.keys())

    def get_stats(self) -> dict[str, Any]:
        """Get manager statistics."""
        return {
            "total_scripts": len(self._scripts),
            "preloaded": self._preloaded,
            "scripts": {
                name: {
                    "sha1": script.sha1[:8],
                    "loaded": script.loaded,
                    "source_lines": len(script.source.splitlines()),
                }
                for name, script in self._scripts.items()
            },
        }


# Singleton instance
_lua_manager: LuaScriptManager | None = None


def get_lua_manager() -> LuaScriptManager:
    """Get the singleton Lua script manager.

    Returns:
        LuaScriptManager: The shared manager instance
    """
    global _lua_manager
    if _lua_manager is None:
        _lua_manager = LuaScriptManager()
    return _lua_manager


def reset_lua_manager() -> None:
    """Reset the singleton manager (for testing)."""
    global _lua_manager
    _lua_manager = None
