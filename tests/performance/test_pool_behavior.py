"""Tests for DB connection pool behavior during AI service operations.

Verifies the C2 optimization: the request-scoped connection is released
before the provider call, not held across inference.

WHAT IS ASSERTED, AND WHY NOT SOURCE TEXT
-----------------------------------------
The C2 invariant is an ORDER of observable events: reads, then persist, then
commit, then (and only then) the provider call. This module drives the real
methods against a fake session and stubbed collaborators, recording every step
in a single ordered event log, and asserts on that log.

The previous revision read ``inspect.getsource`` and asserted
``"await self.db.commit()" in source``, ``"self._invoke_chat" not in
source``, and compared ``source.find("await self._prepare_turn(") <
source.find("self._invoke_chat(")``. That form is wrong twice over: it fails
on harmless change (routing the commit through a helper, renaming the
provider seam, splitting the method), and it passes on the defect — a commit
performed *after* the provider call still satisfies ``"await self.db.commit()"
in source``, and a differently-spelled provider call makes ``.find`` return
-1, which the accompanying ``!= -1`` check then fails for a reason that has
nothing to do with ordering. A character offset in a string is not a
happens-before relation. The event log is.
"""

import asyncio
import inspect
import logging
import time
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.ai.schemas import ChatCompletionRequest
from src.app.modules.ai.service import ChatCompletionService

logger = logging.getLogger(__name__)


class _StubSessionService:
    """Stands in for one of the pre-LLM read collaborators in ``_prepare_turn``."""

    def __init__(self, db: object) -> None:
        self._db = db


class _StubChatSessionService(_StubSessionService):
    async def get_session(self, session_id: int, user_id: int) -> dict:
        self._db.trace.append("read.session")
        return {"model": "test-model", "temperature": 0.1, "max_tokens": 16}


class _StubSettingsService(_StubSessionService):
    async def get_or_create_settings(self, user_id: int) -> dict:
        self._db.trace.append("read.settings")
        return {"temperature": 0.1, "max_tokens": 16, "system_prompt": None}


class _StubMessageService(_StubSessionService):
    async def get_session_messages(self, session_id: int, user_id: int, limit: int = 50) -> dict:
        self._db.trace.append("read.messages")
        return {"data": []}


class _TracedSession:
    """A fake request-scoped session that records the lifecycle events.

    ``commit`` is the release point: everything recorded before it ran while
    the connection was held, everything after it ran without it.
    """

    def __init__(self, trace: list[str]) -> None:
        self.trace = trace
        self.commit_count = 0

    async def commit(self) -> None:
        self.commit_count += 1
        self.trace.append("db.commit")


@asynccontextmanager
async def _local_session_recording(trace: list[str]):
    """Stand-in for ``local_session()`` used for the post-inference write."""
    yield _TracedSession(trace)


def _patch_pre_lm_readers() -> tuple:
    """Patch the three collaborators ``_prepare_turn`` reads through."""
    return (
        patch("src.app.modules.ai.service.ChatSessionService", _StubChatSessionService),
        patch("src.app.modules.ai.service.AISettingsService", _StubSettingsService),
        patch("src.app.modules.ai.service.ChatMessageService", _StubMessageService),
    )


@pytest.fixture
def traced_service():
    """A ChatCompletionService whose every step lands in one ordered trace.

    Built with ``__new__`` so no real session or CRUD is constructed — the
    test owns exactly the collaborators the methods under test touch.
    """
    trace: list[str] = []
    service = ChatCompletionService.__new__(ChatCompletionService)
    service.db = _TracedSession(trace)
    service.provider = MagicMock(provider_name="stub")
    service.trace = trace

    message_service = MagicMock()

    async def _add_message(session_id: int, data: object, **kwargs: object) -> dict:
        trace.append("persist.user_message")
        return {"id": 4242}

    message_service.add_message = _add_message
    service._message_service = message_service
    return service


def _stub_provider_result(message: str = "hello") -> dict:
    return {
        "message": message,
        "token_count": 3,
        "model": "test-model",
        "prompt_tokens": 1,
        "completion_tokens": 2,
    }


class TestConnectionReleasePattern:
    """The C2 invariant: release the connection before the provider call."""

    @pytest.mark.asyncio
    async def test_prepare_turn_commits_and_never_calls_the_provider(self, traced_service) -> None:
        """_prepare_turn ends at the commit and never reaches the provider."""

        async def _must_not_run(*args: object, **kwargs: object) -> None:
            raise AssertionError("_prepare_turn must not call the provider")

        traced_service._invoke_chat = _must_not_run
        readers = _patch_pre_lm_readers()
        with readers[0], readers[1], readers[2]:
            turn = await traced_service._prepare_turn(1, 2, ChatCompletionRequest(message="hi"))

        trace = traced_service.trace
        assert trace.count("db.commit") == 1, f"turn preparation must release the connection once; trace={trace}"
        assert trace[-1] == "db.commit", f"the commit must be the last thing preparation does; trace={trace}"
        assert turn.user_message_id == 4242, "the user message must be persisted before the release"

    @pytest.mark.asyncio
    async def test_prepare_turn_persists_before_it_releases(self, traced_service) -> None:
        """The user message is durable before the connection is handed back."""
        traced_service._invoke_chat = AsyncMock()
        readers = _patch_pre_lm_readers()
        with readers[0], readers[1], readers[2]:
            await traced_service._prepare_turn(1, 2, ChatCompletionRequest(message="hi"))

        trace = traced_service.trace
        assert trace.index("persist.user_message") < trace.index("db.commit"), (
            f"persistence must precede the release; trace={trace}"
        )

    @pytest.mark.asyncio
    async def test_complete_releases_the_connection_before_inference(self, traced_service) -> None:
        """complete() commits, then infers, then writes the response."""

        async def _invoke(
            provider: object,
            model: str,
            history: list,
            temperature: float,
            max_tokens: int,
            system_prompt: object,
        ) -> dict:
            traced_service.trace.append("provider.invoke")
            # Checked from inside the provider call: at this instant the
            # request connection must already be released. That is C2.
            assert traced_service.db.commit_count == 1, (
                f"provider was entered with the connection still held; trace={traced_service.trace}"
            )
            return _stub_provider_result()

        from src.app.modules.ai.token_usage import AITokenUsageService

        traced_service._invoke_chat = _invoke
        traced_service._fallback_candidate = AsyncMock(return_value=None)
        traced_service._get_fallback_provider = AsyncMock(return_value=None)
        traced_service._persist_assistant_message = AsyncMock(
            side_effect=lambda *a, **k: traced_service.trace.append("persist.assistant")
        )

        readers = _patch_pre_lm_readers()
        with (
            readers[0],
            readers[1],
            readers[2],
            patch.object(AITokenUsageService, "save_token_usage", AsyncMock()),
        ):
            result = await traced_service.complete(1, 2, ChatCompletionRequest(message="hi"))

        trace = traced_service.trace
        assert trace.index("db.commit") < trace.index("provider.invoke"), (
            f"the release must precede inference; trace={trace}"
        )
        assert trace.index("provider.invoke") < trace.index("persist.assistant"), (
            f"the response is written after inference; trace={trace}"
        )
        assert result["message"] == "hello"

    @pytest.mark.asyncio
    async def test_complete_writes_the_response_on_a_separate_session(self, traced_service) -> None:
        """The assistant message is persisted on a DIFFERENT session object.

        The old check looked for the literal strings ``"async with
        local_session()"`` and ``"fresh_db"`` — satisfied by any variable name
        whatsoever, including one that held the same released session. What
        matters is that the write does not reuse the request-scoped session.
        """
        write_sessions: list[object] = []

        async def _persist(session_id: int, content: str, token_count: int | None) -> None:
            async with _local_session_recording(traced_service.trace) as fresh_db:
                fresh_db.trace.append("write.on_fresh_session")
                write_sessions.append(fresh_db)

        from src.app.modules.ai.token_usage import AITokenUsageService

        traced_service._invoke_chat = AsyncMock(return_value=_stub_provider_result())
        traced_service._fallback_candidate = AsyncMock(return_value=None)
        traced_service._get_fallback_provider = AsyncMock(return_value=None)
        traced_service._persist_assistant_message = _persist

        readers = _patch_pre_lm_readers()
        with (
            readers[0],
            readers[1],
            readers[2],
            patch("src.app.modules.ai.service.local_session", _local_session_recording),
            patch.object(AITokenUsageService, "save_token_usage", AsyncMock()),
        ):
            await traced_service.complete(1, 2, ChatCompletionRequest(message="hi"))

        assert write_sessions, "the response write must run"
        assert all(session is not traced_service.db for session in write_sessions), (
            "the response write must not reuse the released request session"
        )
        assert "write.on_fresh_session" in traced_service.trace

    @pytest.mark.asyncio
    async def test_complete_stream_releases_the_connection_before_streaming(self, traced_service) -> None:
        """complete_stream() commits before yielding the first chunk."""

        async def _invoke_stream(
            provider: object, model: str, history: list, temperature: float, max_tokens: int, system_prompt: object
        ):
            traced_service.trace.append("provider.stream")
            assert traced_service.db.commit_count == 1, (
                f"streaming began with the connection still held; trace={traced_service.trace}"
            )
            yield {"content": "he", "done": False}
            yield {"content": "llo", "done": True, "token_count": 2}

        traced_service._invoke_chat_stream = _invoke_stream
        traced_service._fallback_candidate = AsyncMock(return_value=None)
        traced_service._persist_assistant_message = AsyncMock()

        readers = _patch_pre_lm_readers()
        with readers[0], readers[1], readers[2]:
            chunks = [c async for c in traced_service.complete_stream(1, 2, ChatCompletionRequest(message="hi"))]

        trace = traced_service.trace
        assert "provider.stream" in trace
        assert trace.index("db.commit") < trace.index("provider.stream"), (
            f"the release must precede streaming; trace={trace}"
        )
        assert [c["content"] for c in chunks] == ["he", "llo"]
        assert chunks[-1]["done"] is True

    def test_persist_assistant_message_is_the_single_shared_write_path(self) -> None:
        """Both entry points share one writer, so the rule cannot drift.

        Interface-level rather than textual: the old assertions read the bodies
        of ``complete`` and ``complete_stream`` separately, so a future writer
        added to one path only would have passed both scans.
        """
        signature = inspect.signature(ChatCompletionService._persist_assistant_message)
        assert list(signature.parameters) == ["self", "session_id", "content", "token_count"]
        assert inspect.iscoroutinefunction(ChatCompletionService._persist_assistant_message)

    @pytest.mark.asyncio
    async def test_persist_assistant_message_opens_and_commits_its_own_session(self) -> None:
        """The shared writer opens a session, writes, and commits that session.

        ``local_session`` is stubbed to hand out a traced session, so the
        write's own commit is observable — and the request session (already
        released by this point) is never committed a second time.
        """
        trace: list[str] = []
        service = ChatCompletionService.__new__(ChatCompletionService)
        request_session = _TracedSession(trace)
        service.db = request_session

        message_service = MagicMock()

        async def _add(session_id: int, data: object, **kwargs: object) -> dict:
            trace.append("add_message")
            return {"id": 1}

        message_service.add_message = _add

        with (
            patch("src.app.modules.ai.service.local_session", lambda: _local_session_recording(trace)),
            patch("src.app.modules.ai.service.ChatMessageService", lambda db: message_service),
            patch("src.app.modules.ai.service.crud_chat_sessions") as crud_sessions,
        ):
            crud_sessions.update = AsyncMock()
            await service._persist_assistant_message(session_id=1, content="done", token_count=2)

        assert trace == ["add_message", "db.commit"], (
            f"the writer must add the message then commit its own session; trace={trace}"
        )
        assert request_session.commit_count == 0, "the already-released request session must not be committed again"
        crud_sessions.update.assert_awaited_once()
        assert crud_sessions.update.await_args.kwargs["db"] is not request_session, (
            "the session update must run on the session the writer opened"
        )



class TestAsyncioGatherParallelFetch:
    """Test P5: asyncio.gather() for parallel fetches."""

    @pytest.mark.asyncio
    async def test_parallel_fetch_faster_than_sequential(self) -> None:
        """Verify parallel fetch is faster than sequential."""
        fetch_delay = 0.05  # 50ms per fetch (higher to avoid Windows 15.6ms clock resolution flakiness)

        async def mock_fetch(name: str) -> dict:
            await asyncio.sleep(fetch_delay)
            return {name: "data"}

        # Sequential timing
        start = time.perf_counter()
        await mock_fetch("session")
        await mock_fetch("settings")
        await mock_fetch("messages")
        sequential_time = time.perf_counter() - start

        # Parallel timing
        start = time.perf_counter()
        await asyncio.gather(
            mock_fetch("session"),
            mock_fetch("settings"),
            mock_fetch("messages"),
        )
        parallel_time = time.perf_counter() - start

        # Parallel should be ~3x faster, but use 1.5x to account for CI/Windows overhead
        speedup = sequential_time / parallel_time
        assert speedup > 1.5, f"Expected >1.5x speedup, got {speedup:.2f}x"

        logger.info(f"Parallel fetch speedup: {speedup:.2f}x")

    @pytest.mark.asyncio
    async def test_gather_returns_all_results(self) -> None:
        """Verify asyncio.gather returns results in correct order."""

        async def fetch_a() -> str:
            await asyncio.sleep(0.02)
            return "A"

        async def fetch_b() -> str:
            await asyncio.sleep(0.01)
            return "B"

        async def fetch_c() -> str:
            await asyncio.sleep(0.015)
            return "C"

        results = await asyncio.gather(fetch_a(), fetch_b(), fetch_c())

        assert results == ["A", "B", "C"]
        logger.info("asyncio.gather: results in argument order")


class TestConcurrentRequestSimulation:
    """Test concurrent request patterns."""

    @pytest.mark.asyncio
    async def test_parallel_requests_pattern(self) -> None:
        """Simulate the connection pattern with multiple requests."""
        active_connections = 0
        max_concurrent = 0
        lock = asyncio.Lock()

        async def simulate_request() -> None:
            nonlocal active_connections, max_concurrent

            # Acquire connection (fetch phase)
            async with lock:
                active_connections += 1
                max_concurrent = max(max_concurrent, active_connections)

            # Release before LLM (C2 pattern)
            async with lock:
                active_connections -= 1

            # Simulate LLM call (no connection held)
            await asyncio.sleep(0.01)

            # Reacquire for save
            async with lock:
                active_connections += 1
                max_concurrent = max(max_concurrent, active_connections)

            # Release after save
            async with lock:
                active_connections -= 1

        # Run 10 concurrent requests
        await asyncio.gather(*[simulate_request() for _ in range(10)])

        # With proper release, max concurrent should be manageable
        assert max_concurrent <= 10
        logger.info(f"Concurrent simulation: max_concurrent={max_concurrent}")
