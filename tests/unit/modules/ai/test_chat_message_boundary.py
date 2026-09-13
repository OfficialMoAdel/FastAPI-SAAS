"""P2-11: ChatMessage is THE provider-boundary parameter.

The AIProvider.generate_text / generate_text_stream seam takes typed
``list[ChatMessage]`` objects — no vestigial ``prompt`` and no untyped
``messages`` riding ``**kwargs``.
"""

import inspect
from unittest.mock import AsyncMock, patch

import pytest
from httpx import Request, Response

from src.app.modules.ai.openai_provider import OpenAIProvider
from src.app.modules.ai.providers import AIProvider, ChatMessage


class TestChatMessageBoundary:
    def test_chatmessage_is_typed_boundary_param(self):
        """ABC signatures take messages: list[ChatMessage] — not prompt/kwargs."""
        for fn in (AIProvider.generate_text, AIProvider.generate_text_stream):
            sig = inspect.signature(fn)
            params = list(sig.parameters)
            assert params[1] == "messages", f"{fn.__name__} must take messages first (after self)"
            assert "prompt" not in params, f"{fn.__name__} must not take a vestigial prompt"
            assert sig.parameters["messages"].annotation is not inspect.Parameter.empty

    @pytest.mark.asyncio
    async def test_provider_payload_carries_model_dumped_messages(self):
        """OpenAI provider converts ChatMessage objects to payload dicts."""
        provider = OpenAIProvider(api_key="sk-test", base_url="http://localhost:11434/v1")
        mock_response = Response(
            status_code=200,
            request=Request("POST", "url"),
            json={"choices": [{"message": {"role": "assistant", "content": "Hi there"}}]},
        )
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
            result = await provider.generate_text(
                messages=[ChatMessage(role="user", content="Hello")],
                model="gpt-4",
                system_prompt="Sys",
            )
            assert result.content == "Hi there"
            payload = mock_post.call_args[1]["json"]
            assert payload["messages"] == [
                {"role": "system", "content": "Sys"},
                {"role": "user", "content": "Hello"},
            ]

    @pytest.mark.asyncio
    async def test_generate_json_wraps_prompt_into_chatmessage(self):
        """generate_json keeps its prompt convenience but wraps into ChatMessage."""
        provider = OpenAIProvider(api_key="sk-test", base_url="http://localhost:11434/v1")
        mock_response = Response(
            status_code=200,
            request=Request("POST", "url"),
            json={"choices": [{"message": {"role": "assistant", "content": '{"key": "value"}'}}]},
        )
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
            result = await provider.generate_json("Prompt", "gpt-4")
            assert result == {"key": "value"}
            payload = mock_post.call_args[1]["json"]
            assert payload["messages"][-1] == {"role": "user", "content": "Prompt"}

    def test_service_invoke_builds_chatmessage_objects(self):
        """_invoke_chat converts dict history to ChatMessage at the seam."""
        from src.app.modules.ai.service import ChatCompletionService

        src = inspect.getsource(ChatCompletionService._invoke_chat)
        assert "ChatMessage(**m) for m in messages" in src
        assert '"messages"' not in src.replace('messages=chat_messages', "")
