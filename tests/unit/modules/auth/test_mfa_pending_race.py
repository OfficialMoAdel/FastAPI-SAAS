"""Bead FastAPI_saas-mchw: a pending MFA token must arbitrate before it burns codes.

The defect this pins: ``complete_login`` peeked the pending token, ran the
factor verify (which, on a backup code, removes the hash and COMMITS), and only
then consumed the token. ``peek`` is a plain GET, so N concurrent attempts
carrying N distinct backup codes all passed it: every one of them burnt a code
on the way to losing the GETDEL.

These tests drive the real orchestration. Only the two I/O edges are doubled,
and each double reproduces the property that matters:
``RaceStore`` models the Lua semantics (SET NX EX claim, compare-and-delete
release, atomic GETDEL) and ``RaceDB`` models a genuine SELECT..FOR UPDATE row
lock. Both yield to the event loop on every roundtrip -- a double that never
suspends lets each racer run to completion in turn, which is precisely what
hides the race.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi import Request, Response

from src.app.modules.auth.schemas import MFACompleteLoginRequest
from src.app.modules.auth.services import mfa
from src.app.modules.auth.services.mfa import MFAService
from src.app.modules.auth.services.tokens import IssuedSession
from src.app.shared.exceptions import UnauthorizedError

USER_ID = 4242
TOKEN = "a" * 32
LOCKOUT_KEY = f"mfa_attempts:{USER_ID}"


async def _io_yield() -> None:
    """Model the await point a real socket read creates."""
    await asyncio.sleep(0)


def _hash_now(service: MFAService, code: str) -> str:
    """Drive a coroutine that returns without suspending to its value."""
    try:
        service._hash_backup_code(code, USER_ID).send(None)
    except StopIteration as stop:
        return stop.value
    raise AssertionError("expected _hash_backup_code to return immediately")


def _victim_codes() -> list[str]:
    """Backup codes in the exact shape ``_generate_backup_codes`` emits."""
    out = []
    for i in range(mfa.BACKUP_CODE_COUNT):
        part = f"{i:05X}ABCDE"[:20].ljust(20, "F")
        out.append(f"BC-{part[:5]}-{part[5:10]}-{part[10:15]}-{part[15:20]}")
    return out


class RaceStore:
    """Models ``PendingTokenStore`` against one shared keyspace.

    Mirrors the three guarantees the service depends on, each of which comes
    from a Lua script or a Redis primitive rather than from Python ordering:
    ``peek`` is a non-mutating GET, ``consume`` is an atomic GETDEL, ``claim``
    is a ``SET NX EX`` that exactly one caller can win, and ``release_claim``
    deletes only when the stored nonce still matches the caller's own.
    """

    def __init__(self) -> None:
        self.store: dict[str, dict] = {}
        self.claims: dict[str, str] = {}
        self.released: list[str] = []
        self.consume_calls = 0

    def _key(self, token: str) -> str:
        return f"mfa_pending:{token}"

    def _claim_key(self, token: str) -> str:
        return f"{self._key(token)}:verify_claim"

    async def create(self, token: str, data: dict) -> None:
        self.store[self._key(token)] = data

    async def peek(self, token: str) -> dict | None:
        await _io_yield()
        data = self.store.get(self._key(token))
        return dict(data) if data is not None else None

    async def remaining_ttl(self, token: str) -> int:
        await _io_yield()
        return 300 if self._key(token) in self.store else -2

    async def consume(self, token: str) -> dict | None:
        # Atomic GETDEL: no await between the read and the delete.
        await _io_yield()
        self.consume_calls += 1
        return self.store.pop(self._key(token), None)

    async def claim(self, token: str, ttl_seconds: int, nonce: str) -> bool:
        await _io_yield()
        key = self._claim_key(token)
        if key in self.claims:
            return False
        self.claims[key] = nonce
        return True

    async def release_claim(self, token: str, nonce: str) -> bool:
        key = self._claim_key(token)
        if self.claims.get(key) == nonce:
            del self.claims[key]
            self.released.append(nonce)
            return True
        return False


class _Result:
    """The ``AsyncResult`` surface the two seams on this path actually use."""

    def __init__(self, row) -> None:
        self._row = row

    def scalar_one_or_none(self):
        return self._row

    def first(self):
        # FastCRUD's ``get`` does ``dict(result._mapping)`` on what first()
        # returns, so the CRUD-shaped row must carry _mapping.
        if self._row is None:
            return None
        mapped = SimpleNamespace(**vars(self._row))
        mapped._mapping = dict(vars(self._row))
        return mapped


class RaceDB:
    """Models ``AsyncSession`` with a real ``SELECT .. FOR UPDATE`` row lock.

    The lock is taken in ``execute`` and held until the transaction ends, so N
    racers serialise on the MFA row exactly as Postgres does. That row lock is
    what makes each individual redemption safe; the pending-token claim is a
    separate concern -- it is the only thing that stops a racer from *reaching*
    this lock. A barrier releases every racer simultaneously so the
    interleaving under test is the real one and not a scheduling artefact.
    """

    def __init__(self, service: MFAService, expected_racers: int) -> None:
        self.backup_codes = [_hash_now(service, code) for code in _victim_codes()]
        self.lock = asyncio.Lock()
        self.commits = 0
        self.row_locks = 0
        self.lockout_deletes = 0
        self._rows: dict[int, SimpleNamespace] = {}
        self._held: set[int] = set()
        self._arrived = 0
        self._expected = expected_racers
        self._barrier = asyncio.Event()

    async def execute(self, stmt):
        await _io_yield()
        # Only _verify_backup_code's SELECT .. FOR UPDATE takes the row lock;
        # the plain FastCRUD reads that share this seam do not.
        if getattr(stmt, "_for_update_arg", None) is None:
            row = SimpleNamespace(
                user_id=USER_ID,
                id=7,
                type="totp",
                secret=getattr(self, "secret", None),
                backup_codes=list(self.backup_codes),
            )
            return _Result(row)

        self.row_locks += 1
        self._arrived += 1
        if self._arrived >= self._expected:
            self._barrier.set()
        try:
            await asyncio.wait_for(self._barrier.wait(), timeout=2.0)
        except TimeoutError:
            # Post-fix the losing racers never reach the row lock, so the
            # barrier can no longer fill. Timing out is the expected outcome.
            pass

        await self.lock.acquire()
        row = SimpleNamespace(
            user_id=USER_ID, type="totp", backup_codes=self.backup_codes, last_used_at=None, id=7
        )
        self._rows[id(row)] = row
        self._held.add(id(row))
        return _Result(row)

    async def commit(self) -> None:
        self.commits += 1
        self._flush()

    async def rollback(self) -> None:
        self._flush()

    def _flush(self) -> None:
        for key in list(self._held):
            # The list _verify_backup_code assigned is the committed truth.
            self.backup_codes = self._rows[key].backup_codes
            self._held.discard(key)
            self.lock.release()

    async def get(self, model, pk):
        return SimpleNamespace(user_id=USER_ID, last_used_at=None)


def _make_env(service, store, db, redis):
    """The patches every attempt in these tests runs under."""
    issued = IssuedSession(access_token="access", refresh_token="refresh")
    user = {"id": USER_ID, "username": "victim", "is_active": True, "is_deleted": False}
    dumped = MagicMock()
    dumped.model_dump.return_value = user
    return (
        patch.object(mfa, "check", new_callable=AsyncMock),
        patch.object(mfa, "get_redis", return_value=redis),
        patch.object(mfa, "get_client_ip", return_value="192.0.2.1"),
        patch.object(mfa, "get_mfa_pending_token_manager", return_value=store),
        patch.object(mfa, "audit", new_callable=AsyncMock),
        patch("src.app.modules.users.UserDirectory", return_value=Mock(member=AsyncMock(return_value=dumped))),
        patch.object(mfa.TokenIssuer, "issue_session", new_callable=AsyncMock, return_value=issued),
        patch.object(mfa.settings.auth, "MFA_ENFORCE_IP_BINDING", True),
        issued,
    )


def _pending() -> dict:
    return {
        "scope": "mfa_pending",
        "user_id": USER_ID,
        "username": "victim",
        "ip_address": "192.0.2.1",
    }


async def _attempt(service, store, db, redis, code: str, totp: str | None = None):
    """One complete_login call, with the row lock always released after."""
    *patches, issued = _make_env(service, store, db, redis)
    request = MFACompleteLoginRequest(mfa_token=TOKEN, backup_code=code, totp_token=totp)
    http = Request({"type": "http", "headers": [], "client": ("192.0.2.1", 1234)})
    try:
        for p in patches:
            p.start()
        return await service.complete_login(request, db, fastapi_request=http, response=Response())
    finally:
        db._flush()
        for p in patches:
            p.stop()


class RecordingRedis:
    """The suite's Redis double, wrapped so tests can assert on its deletes.

    The durable lockout check runs real Lua through ``evalsha``, so this
    delegates to the ``mock_redis`` fixture rather than re-implementing it.
    Only ``delete`` is intercepted, because "did a losing racer clear the
    lockout counter" is the question these tests ask.
    """

    def __init__(self, inner) -> None:
        self._inner = inner
        self.deleted: list[str] = []

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def delete(self, key: str):
        await _io_yield()
        self.deleted.append(key)
        return await self._inner.delete(key)


@pytest.mark.asyncio
class TestMfaBackupCodeRace:
    """The core property: one pending token, one code redeemed."""

    async def test_concurrent_racers_burn_only_the_winners_code(self, mock_redis) -> None:
        """N racers with N distinct real codes destroy exactly ONE code.

        Before the fix this burned all N: every racer passed the non-mutating
        peek, redeemed its own backup code (a committed mutation), and only then
        lost the atomic GETDEL.
        """
        service = MFAService()
        store = RaceStore()
        await store.create(TOKEN, _pending())
        db = RaceDB(service, expected_racers=mfa.BACKUP_CODE_COUNT)
        redis = RecordingRedis(mock_redis)
        codes = _victim_codes()

        results = await asyncio.gather(
            *(_attempt(service, store, db, redis, code) for code in codes),
            return_exceptions=True,
        )

        sessions = sum(1 for r in results if not isinstance(r, BaseException))
        remaining = len(db.backup_codes)

        assert sessions == 1, "GETDEL must still issue exactly one session"
        assert remaining == mfa.BACKUP_CODE_COUNT - 1, (
            f"a losing racer burned a code: {mfa.BACKUP_CODE_COUNT - remaining} of "
            f"{mfa.BACKUP_CODE_COUNT} codes were destroyed"
        )
        # The losers never reached the factor verify at all.
        assert db.row_locks == 1
        assert db.commits == 1

    async def test_loser_does_not_clear_the_lockout_counter(self, mock_redis) -> None:
        """A refused racer must not reset the account's failure counter.

        ``_verify_backup_code`` deletes ``mfa_attempts:{user_id}`` on success
        (:677-682) so a genuine redemption does not leave the user one step
        from lockout. If a loser could reach it, an attacker could launder the
        victim's lockout counter down with guesses that all failed.
        """
        service = MFAService()
        store = RaceStore()
        await store.create(TOKEN, _pending())
        db = RaceDB(service, expected_racers=mfa.BACKUP_CODE_COUNT)
        redis = RecordingRedis(mock_redis)
        await redis.set(LOCKOUT_KEY, 4)  # victim is one step from lockout
        codes = _victim_codes()

        await asyncio.gather(
            *(_attempt(service, store, db, redis, code) for code in codes),
            return_exceptions=True,
        )

        # The winner legitimately clears it; every loser must not have.
        assert redis.deleted.count(LOCKOUT_KEY) <= 1
        assert redis.deleted == [LOCKOUT_KEY]

    async def test_winner_code_is_consumed_exactly_once(self, mock_redis) -> None:
        """The winner gets a session and its own code is gone from storage."""
        service = MFAService()
        store = RaceStore()
        await store.create(TOKEN, _pending())
        db = RaceDB(service, expected_racers=mfa.BACKUP_CODE_COUNT)
        redis = RecordingRedis(mock_redis)

        results = await asyncio.gather(
            *(_attempt(service, store, db, redis, code) for code in _victim_codes()),
            return_exceptions=True,
        )
        winner = next(r for r in results if not isinstance(r, BaseException))

        assert winner.access_token == "access"
        assert len(db.backup_codes) == mfa.BACKUP_CODE_COUNT - 1
        assert len(set(db.backup_codes)) == len(db.backup_codes), "no duplicate entries"


@pytest.mark.asyncio
class TestClaimLifecycle:
    """A wrong code must not cost the user their pending challenge."""

    async def test_failed_verify_releases_the_claim_for_a_retry(self, mock_redis) -> None:
        """Typo -> released -> the SAME token still completes a real code.

        This is the asymmetry the fix has to respect: claim-first stops the
        race, but a claim that was never released would mean one typo forfeits
        the user's entire login.
        """
        service = MFAService()
        store = RaceStore()
        await store.create(TOKEN, _pending())
        db = RaceDB(service, expected_racers=1)
        redis = RecordingRedis(mock_redis)
        good = _victim_codes()[0]

        # First attempt: a wrong code.
        # verify_totp_token rejects a bad factor by RAISING, which is the path
        # that must still hand the challenge back.
        with pytest.raises(UnauthorizedError, match="Invalid MFA token"):
            await _attempt(service, store, db, redis, "BC-ZZZZZ-ZZZZZ-ZZZZZ-ZZZZZ")

        # The challenge survived and the claim was handed back.
        assert await store.peek(TOKEN) is not None
        assert store.claims == {}
        assert len(store.released) == 1

        # Second attempt on the very same token succeeds.
        assert (await _attempt(service, store, db, redis, good)).access_token == "access"
        assert len(db.backup_codes) == mfa.BACKUP_CODE_COUNT - 1

    async def test_successful_verify_leaves_no_claim_behind(self, mock_redis) -> None:
        """The winner does not release: its claim is consumed by the flow.

        Releasing on success would reopen the window for anyone still in flight
        on the same token.
        """
        service = MFAService()
        store = RaceStore()
        await store.create(TOKEN, _pending())
        db = RaceDB(service, expected_racers=1)
        redis = RecordingRedis(mock_redis)

        await _attempt(service, store, db, redis, _victim_codes()[0])

        assert store.released == []

    async def test_release_cannot_steal_another_holders_claim(self) -> None:
        """Compare-and-delete: a stale releaser cannot free a newer claim.

        Without the nonce check, a holder whose claim had already lapsed could
        delete the claim a *different* request had since taken -- reintroducing
        exactly the two-holders window the claim exists to prevent.
        """
        store = RaceStore()
        await store.create(TOKEN, _pending())

        assert await store.claim(TOKEN, 30, "nonce-a") is True
        # 'nonce-b' loses the claim, then a's claim lapses and b re-takes it.
        assert await store.claim(TOKEN, 30, "nonce-b") is False
        store.claims[store._claim_key(TOKEN)] = "nonce-b"

        assert await store.release_claim(TOKEN, "nonce-a") is False
        assert store._claim_key(TOKEN) in store.claims
        assert await store.release_claim(TOKEN, "nonce-b") is True


@pytest.mark.asyncio
class TestSingleRequestUnregressed:
    """The pre-existing happy paths still work; the claim adds no friction."""

    @pytest.mark.parametrize("factor", ["backup_code", "totp"])
    async def test_correct_factor_completes_login(self, mock_redis, factor: str) -> None:
        """A correct code -- of either kind -- still completes the login."""
        service = MFAService()
        store = RaceStore()
        await store.create(TOKEN, _pending())
        db = RaceDB(service, expected_racers=1)
        redis = RecordingRedis(mock_redis)

        if factor == "backup_code":
            result = await _attempt(service, store, db, redis, _victim_codes()[0])
            assert result.access_token == "access"
            assert len(db.backup_codes) == mfa.BACKUP_CODE_COUNT - 1
        else:
            # The TOTP leg is the door's other success path. It mutates only
            # last_used_at, but it must reach the same claim gate and, when it
            # succeeds, consume the challenge exactly as the backup leg does.
            import pyotp

            secret = pyotp.random_base32()
            db.secret = await service._encrypt_secret(secret)
            code = pyotp.TOTP(secret).now()
            result = await _attempt(service, store, db, redis, None, totp=code)
            assert result.access_token == "access"
            assert store.consume_calls == 1
