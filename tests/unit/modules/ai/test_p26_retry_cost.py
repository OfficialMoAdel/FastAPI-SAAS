"""P2-6 regression tests: Retry-After honored, cost logging wired, model widened."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.app.core.utils.retry import with_retry
from src.app.modules.ai.providers import AIProvider, AIProviderError
from src.app.modules.ai.schemas import ChatCompletionRequest
from src.app.modules.ai.service import ChatCompletionService


class _StubProvider(AIProvider):
    """Minimal concrete provider to exercise the base _map_error."""

    @property
    def provider_name(self) -> str:
        return "openai"

    async def generate_text(
        self,
        prompt: str,
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> Any:
        raise NotImplementedError

    def generate_text_stream(
        self,
        prompt: str,
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> Any:
        raise NotImplementedError

    async def embed_text(self, text: str, model: str, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def list_models(self) -> list[str]:
        raise NotImplementedError

    async def health_check(self) -> bool:
        raise NotImplementedError


def _http_status_error(status: int, retry_after: str | None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.test/v1/chat")
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    response = httpx.Response(status, headers=headers, request=request)
    return httpx.HTTPStatusError("err", request=request, response=response)


class TestP26MapErrorRetryAfter:
    """_map_error must surface Retry-After on 429 (P2-6)."""

    def test_429_with_retry_after_parsed(self) -> None:
        err = _StubProvider()._map_error(_http_status_error(429, "2"), "chat")
        assert isinstance(err, AIProviderError)
        assert err.error_code == "429"
        assert err.retry_after == 2.0

    def test_429_without_header_is_none(self) -> None:
        err = _StubProvider()._map_error(_http_status_error(429, None), "chat")
        assert err.retry_after is None

    def test_429_with_garbage_header_is_none(self) -> None:
        err = _StubProvider()._map_error(_http_status_error(429, "not-a-number"), "chat")
        assert err.retry_after is None

    def test_non_429_never_carries_retry_after(self) -> None:
        err = _StubProvider()._map_error(_http_status_error(500, "2"), "chat")
        assert err.error_code == "500"
        assert err.retry_after is None


class TestP26WithRetryHonorsServerWait:
    """with_retry must sleep the server-directed wait when present (P2-6)."""

    @pytest.mark.asyncio
    async def test_retry_after_attr_used(self) -> None:
        calls = {"n": 0}

        async def flaky() -> str:
            calls["n"] += 1
            if calls["n"] == 1:
                raise AIProviderError("busy", error_code="429", provider="openai", retry_after=0.05)
            return "ok"

        with patch("src.app.core.utils.retry.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            assert await with_retry(
                flaky, max_attempts=2, delay_seconds=30.0, retry_after_attr="retry_after", jitter=False
            ) == "ok"
        mock_sleep.assert_awaited_once_with(0.05)

    @pytest.mark.asyncio
    async def test_retry_after_capped(self) -> None:
        async def always_fail() -> str:
            raise AIProviderError("busy", error_code="429", provider="openai", retry_after=9999.0)

        with patch("src.app.core.utils.retry.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            with pytest.raises(AIProviderError):
                await with_retry(
                    always_fail,
                    max_attempts=2,
                    delay_seconds=30.0,
                    retry_after_attr="retry_after",
                    max_retry_after=120.0,
                    jitter=False,
                )
        mock_sleep.assert_awaited_once_with(120.0)

    @pytest.mark.asyncio
    async def test_no_attr_falls_back_to_backoff(self) -> None:
        async def always_fail() -> str:
            raise AIProviderError("boom", error_code="500", provider="openai")

        with patch("src.app.core.utils.retry.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            with pytest.raises(AIProviderError):
                await with_retry(
                    always_fail, max_attempts=2, delay_seconds=0.5, retry_after_attr="retry_after", jitter=False
                )
        mock_sleep.assert_awaited_once_with(0.5)


class TestP26CostLoggingWired:
    """complete() must log the provider token breakdown (P2-6)."""

    def _make_service(self) -> tuple[ChatCompletionService, MagicMock]:
        mock_db = AsyncMock()
        service = ChatCompletionService(db=mock_db)

        provider = MagicMock(spec=["provider_name", "generate_text", "close"])
        provider.provider_name = "openai"
        response = MagicMock()
        response.content = "Hello!"
        response.model = "gpt-4o"
        response.finish_reason = "stop"
        usage = MagicMock()
        usage.total_tokens = 20
        usage.prompt_tokens = 7
        usage.completion_tokens = 13
        response.token_usage = usage
        provider.generate_text = AsyncMock(return_value=response)
        service.provider = provider
        # complete() saves the user message via the instance-level
        # _message_service (same override the b7 harness uses).
        service._message_service = MagicMock()
        service._message_service.add_message = AsyncMock(return_value={"id": 9})
        return service, mock_db

    @pytest.mark.asyncio
    async def test_complete_logs_token_breakdown(self) -> None:
        service, mock_db = self._make_service()
        request = ChatCompletionRequest(message="Hello", model="gpt-4o")

        with patch("src.app.modules.ai.service.crud_chat_sessions") as mock_crud, patch(
            "src.app.modules.ai.service.ChatSessionService"
        ) as MockSessionService, patch(
            "src.app.modules.ai.service.AISettingsService"
        ) as MockSettingsService, patch(
            "src.app.modules.ai.service.ChatMessageService"
        ) as MockMsgService, patch(
            "src.app.modules.ai.service.truncate_context", return_value=[]
        ), patch(
            "src.app.modules.ai.service.local_session"
        ) as mock_local_session, patch(
            "src.app.modules.ai.token_usage.log_token_usage"
        ) as mock_log_usage:
            mock_crud.update = AsyncMock()
            mock_session_inst = MagicMock()
            mock_session_inst.get_session = AsyncMock(return_value={"id": 1, "model": "gpt-4o", "user_id": 1})
            MockSessionService.return_value = mock_session_inst
            mock_settings_inst = MagicMock()
            mock_settings_inst.get_or_create_settings = AsyncMock(
                return_value={"model": "gpt-4o", "temperature": 0.7, "max_tokens": 2048}
            )
            MockSettingsService.return_value = mock_settings_inst
            mock_msg_inst = MagicMock()
            mock_msg_inst.get_session_messages = AsyncMock(return_value={"data": [], "total": 0})
            mock_msg_inst.add_message = AsyncMock()
            MockMsgService.return_value = mock_msg_inst
            mock_fresh_db = AsyncMock()
            mock_fresh_db.__aenter__ = AsyncMock(return_value=mock_fresh_db)
            mock_fresh_db.__aexit__ = AsyncMock(return_value=None)
            mock_fresh_db.commit = AsyncMock()
            mock_local_session.return_value = mock_fresh_db

            result = await service.complete(session_id=1, user_id=1, request=request)

        assert result["token_count"] == 20
        mock_log_usage.assert_called_once_with(
            provider="openai",
            model="gpt-4o",
            prompt_tokens=7,
            completion_tokens=13,
            user_id=1,
            tenant_id=None,
            session_id=1,
        )


class TestP26ModelWidened:
    """AIModelType must accept non-Ollama names (P2-6)."""

    def test_accepts_openai_model_name(self) -> None:
        req = ChatCompletionRequest(message="x", model="gpt-4o")
        assert req.model == "gpt-4o"

    def test_rejects_empty_model_name(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ChatCompletionRequest(message="x", model="")
