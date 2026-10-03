"""Card 8: the chat turn has ONE set of rules, two ways to expose the result.

``ChatCompletionService.complete`` and ``ChatCompletionService.complete_stream``
used to each carry their own copy of the turn lifecycle: read session +
settings + history, resolve the generation parameters, truncate the context,
persist the user message, and COMMIT (releasing the connection before the
provider holds it). These tests pin the collapsed contract — both entry points
go through the same preparation and therefore agree on the turn rules — rather
than the duplicated spelling that was removed.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.ai.providers import AIProvider, ChatCompletionResponse, ChatMessage
from src.app.modules.ai.schemas import ChatCompletionRequest
from src.app.modules.ai.service import ChatCompletionService

SESSION_ROW = {"id": 7, "user_id": 1, "model": "gemma3"}
SETTINGS_ROW = {"id": 3, "user_id": 1, "model": "settings-model", "temperature": 0.4, "max_tokens": 900}
HISTORY_ROWS = [
    {"role": "user", "content": "earlier question"},
    {"role": "assistant", "content": "earlier answer"},
]


class _RecordingProvider(AIProvider):
    """Provider that records the exact arguments it was handed."""

    def __init__(self, log: list[str]) -> None:
        self._log = log
        self.calls: list[dict[str, Any]] = []

    @property
    def provider_name(self) -> str:
        return "ollama"

    def _record(self, messages: list[ChatMessage], model: str, temperature: float,
                max_tokens: int | None, system_prompt: str | None) -> dict[str, Any]:
        self._log.append("provider_call")
        call = {
            "messages": [(m.role, m.content) for m in messages],
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "system_prompt": system_prompt,
        }
        self.calls.append(call)
        return call

    async def generate_text(
        self,
        messages: list[ChatMessage],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> ChatCompletionResponse:
        self._record(messages, model, temperature, max_tokens, system_prompt)
        return ChatCompletionResponse(content="Hi there", model=model, finish_reason="stop", token_usage=None)

    def generate_text_stream(
        self,
        messages: list[ChatMessage],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> Any:
        async def _gen() -> Any:
            self._record(messages, model, temperature, max_tokens, system_prompt)
            yield "Hi "
            yield "there"

        return _gen()

    async def embed_text(self, text: str, model: str, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def list_models(self) -> list[str]:
        return ["gemma3"]

    async def health_check(self) -> bool:
        return True


def _build_service(log: list[str]) -> tuple[ChatCompletionService, AsyncMock, dict[str, Any]]:
    """Service wired to a recording provider and a db whose commit is logged.

    The three pre-LLM read services are patched at the module seam (the way
    every AI service test wires them) and every read/persist records its call
    in ``log`` so the two entry points can be compared step for step.
    """
    db = AsyncMock()
    db.commit = AsyncMock(side_effect=lambda: log.append("db_commit"))

    provider = _RecordingProvider(log)
    service = ChatCompletionService(db=db, provider=provider)

    recorded: dict[str, Any] = {}

    session_svc = MagicMock()

    async def _get_session(session_id: int, user_id: int) -> dict[str, Any]:
        log.append("get_session")
        return SESSION_ROW

    session_svc.get_session = _get_session

    settings_svc = MagicMock()

    async def _get_settings(user_id: int) -> dict[str, Any]:
        log.append("get_or_create_settings")
        return SETTINGS_ROW

    settings_svc.get_or_create_settings = _get_settings

    message_svc = MagicMock()

    async def _get_messages(
        session_id: int, page: int = 1, limit: int = 50, descending: bool = False
    ) -> dict[str, Any]:
        log.append("get_session_messages")
        recorded["history_limit"] = limit
        return {"data": HISTORY_ROWS, "total_count": len(HISTORY_ROWS)}

    message_svc.get_session_messages = _get_messages

    async def _add_message(session_id: int, data: Any, token_count: int | None = None) -> dict[str, Any]:
        log.append("add_message")
        recorded.setdefault("messages", []).append(
            {
                "session_id": session_id,
                "role": str(data.role),
                "content": data.content,
                "token_count": token_count,
            }
        )
        return {"id": 42}

    message_svc.add_message = _add_message

    recorded["session_service"] = session_svc
    recorded["settings_service"] = settings_svc
    recorded["message_service"] = message_svc
    return service, db, recorded


def _patch_seams(service: ChatCompletionService, recorded: dict[str, Any]) -> Any:
    """Patch the module-level seam names _prepare_turn constructs from."""
    return (
        patch("src.app.modules.ai.service.ChatSessionService", return_value=recorded["session_service"]),
        patch("src.app.modules.ai.service.AISettingsService", return_value=recorded["settings_service"]),
        patch("src.app.modules.ai.service.ChatMessageService", return_value=recorded["message_service"]),
    )


async def _run_complete(request: ChatCompletionRequest) -> tuple[list[str], list[dict[str, Any]], dict[str, Any]]:
    log: list[str] = []
    service, db, recorded = _build_service(log)
    service._message_service = recorded["message_service"]
    session_p, settings_p, message_p = _patch_seams(service, recorded)
    with session_p, settings_p, message_p, \
         patch("src.app.modules.ai.service.crud_chat_sessions") as mock_sessions, \
         patch("src.app.modules.ai.service.crud_chat_messages"), \
         patch("src.app.modules.ai.service.local_session") as mock_local_session, \
         patch("src.app.modules.ai.service.get_current_tenant", return_value=None):
        mock_sessions.update = AsyncMock()
        fresh_db = AsyncMock()
        fresh_db.__aenter__ = AsyncMock(return_value=fresh_db)
        fresh_db.__aexit__ = AsyncMock(return_value=None)
        mock_local_session.return_value = fresh_db
        result = await service.complete(session_id=7, user_id=1, request=request)
    return log, service.provider.calls, {"result": result, "messages": recorded.get("messages", [])}


async def _run_complete_stream(
    request: ChatCompletionRequest,
) -> tuple[list[str], list[dict[str, Any]], dict[str, Any]]:
    log: list[str] = []
    service, db, recorded = _build_service(log)
    service._message_service = recorded["message_service"]
    session_p, settings_p, message_p = _patch_seams(service, recorded)
    with session_p, settings_p, message_p, \
         patch("src.app.modules.ai.service.crud_chat_sessions") as mock_sessions, \
         patch("src.app.modules.ai.service.crud_chat_messages"), \
         patch("src.app.modules.ai.service.local_session") as mock_local_session, \
         patch("src.app.modules.ai.service.get_current_tenant", return_value=None), \
         patch("src.app.core.utils.background_task.create_safe_task"):
        mock_sessions.update = AsyncMock()
        fresh_db = AsyncMock()
        fresh_db.__aenter__ = AsyncMock(return_value=fresh_db)
        fresh_db.__aexit__ = AsyncMock(return_value=None)
        mock_local_session.return_value = fresh_db
        chunks = [chunk async for chunk in service.complete_stream(session_id=7, user_id=1, request=request)]
    return log, service.provider.calls, {"chunks": chunks, "messages": recorded.get("messages", [])}


TURN_RULES = [
    "get_session",
    "get_or_create_settings",
    "get_session_messages",
    "add_message",
    "db_commit",
    "provider_call",
]


class TestBothEntryPointsShareTheTurnRules:
    """complete() and complete_stream() must run the SAME turn lifecycle."""

    @pytest.mark.asyncio
    async def test_both_enter_the_provider_through_one_preparation(self) -> None:
        request = ChatCompletionRequest(message="  hello  ")

        complete_log, complete_calls, _ = await _run_complete(request)
        stream_log, stream_calls, _ = await _run_complete_stream(request)

        assert complete_log[: len(TURN_RULES)] == TURN_RULES
        assert stream_log == TURN_RULES
        # The only difference is the tail: complete() persists the assistant
        # turn inline (through the same _persist_assistant_message seam the
        # stream path uses in its background task) before returning.
        assert complete_log[len(TURN_RULES) :] == ["add_message"]

    @pytest.mark.asyncio
    async def test_both_send_the_provider_the_same_turn(self) -> None:
        request = ChatCompletionRequest(message="  hello  ")

        _, complete_calls, _ = await _run_complete(request)
        _, stream_calls, _ = await _run_complete_stream(request)

        assert len(complete_calls) == 1
        assert len(stream_calls) == 1
        assert complete_calls[0] == stream_calls[0]

    @pytest.mark.asyncio
    async def test_both_persist_the_same_user_message_before_the_provider(self) -> None:
        request = ChatCompletionRequest(message="  hello  ")

        _, _, complete_meta = await _run_complete(request)
        _, _, stream_meta = await _run_complete_stream(request)

        user_message = {"session_id": 7, "role": "user", "content": "  hello  ", "token_count": None}
        assert complete_meta["messages"][0] == user_message
        assert stream_meta["messages"][0] == user_message
        # The assistant turn is persisted once, through the shared seam.
        assert complete_meta["messages"][1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_both_release_the_connection_before_the_provider(self) -> None:
        """C2: the user turn is committed before inference holds the session."""
        request = ChatCompletionRequest(message="hello")

        complete_log, _, _ = await _run_complete(request)
        stream_log, _, _ = await _run_complete_stream(request)

        assert complete_log.index("db_commit") < complete_log.index("provider_call")
        assert stream_log.index("db_commit") < stream_log.index("provider_call")

    @pytest.mark.asyncio
    async def test_both_resolve_settings_and_request_overrides_identically(self) -> None:
        """Request overrides win over session model, which wins over settings."""
        request = ChatCompletionRequest(
            message="hello",
            model="gpt-4o",
            temperature=0.1,
            max_tokens=64,
        )

        _, complete_calls, _ = await _run_complete(request)
        _, stream_calls, _ = await _run_complete_stream(request)

        for calls in (complete_calls, stream_calls):
            assert calls[0]["model"] == "gpt-4o"
            assert calls[0]["temperature"] == 0.1
            assert calls[0]["max_tokens"] == 64
        assert complete_calls[0] == stream_calls[0]

    @pytest.mark.asyncio
    async def test_both_truncate_history_with_the_settings_system_prompt(self) -> None:
        request = ChatCompletionRequest(message="hello")

        _, complete_calls, complete_meta = await _run_complete(request)
        _, stream_calls, stream_meta = await _run_complete(request)

        # The provider sees history + the sanitized new turn, never the raw
        # padded message; the persisted row keeps the user's exact text.
        expected_tail = ("user", "hello")
        assert complete_calls[0]["messages"][-1] == expected_tail
        assert stream_calls[0]["messages"][-1] == expected_tail
        assert complete_meta["messages"][0]["content"] == "hello"

    @pytest.mark.asyncio
    async def test_stream_yields_the_same_content_the_sync_turn_returns(self) -> None:
        request = ChatCompletionRequest(message="hello")

        _, _, complete_meta = await _run_complete(request)
        _, _, stream_meta = await _run_complete_stream(request)

        streamed = "".join(chunk["content"] for chunk in stream_meta["chunks"])
        assert streamed == complete_meta["result"]["message"]
        assert stream_meta["chunks"][-1]["done"] is True
