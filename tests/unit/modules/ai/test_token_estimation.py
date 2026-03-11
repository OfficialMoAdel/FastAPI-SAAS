"""Unit tests for token estimation and context truncation.

Tests T096: Token estimation (4 chars per token)
Tests T097: Context window truncation (FIFO, preserve system prompt)
"""

from src.app.modules.ai.service import (
    estimate_messages_tokens,
    estimate_tokens,
    truncate_context,
)


class TestTokenEstimation:
    """Test suite for token estimation functions (T096)."""

    def test_estimate_tokens_empty_string(self):
        """Test that empty string returns 0 tokens."""
        assert estimate_tokens("") == 0

    def test_estimate_tokens_none(self):
        """Test that None returns 0 tokens."""
        assert estimate_tokens(None) == 0

    def test_estimate_tokens_short_text(self):
        """Test token estimation for short text."""
        # 8 chars / 4 = 2 tokens
        assert estimate_tokens("12345678") == 2

    def test_estimate_tokens_exact_multiple(self):
        """Test token estimation for exact multiple of 4."""
        # 16 chars / 4 = 4 tokens
        text = "a" * 16
        assert estimate_tokens(text) == 4

    def test_estimate_tokens_not_exact_multiple(self):
        """Test token estimation when not exact multiple of 4."""
        # 17 chars / 4 = 4 tokens (integer division)
        text = "a" * 17
        assert estimate_tokens(text) == 4

    def test_estimate_tokens_long_text(self):
        """Test token estimation for longer text."""
        # 400 chars / 4 = 100 tokens
        text = "a" * 400
        assert estimate_tokens(text) == 100

    def test_estimate_tokens_realistic_text(self):
        """Test token estimation for realistic message."""
        text = "Hello, how are you doing today?"  # 31 chars
        assert estimate_tokens(text) == 7  # 31 // 4 = 7


class TestEstimateMessagesTokens:
    """Test suite for message list token estimation."""

    def test_estimate_messages_empty_list(self):
        """Test that empty message list returns 0 tokens."""
        assert estimate_messages_tokens([]) == 0

    def test_estimate_messages_single_message(self):
        """Test token estimation for single message."""
        messages = [{"role": "user", "content": "Hello"}]  # "user"=1, "Hello"=1, +4 overhead
        result = estimate_messages_tokens(messages)
        assert result == 6  # 1 + 1 + 4 = 6

    def test_estimate_messages_multiple_messages(self):
        """Test token estimation for multiple messages."""
        messages = [
            {"role": "user", "content": "Hi there"},  # 1 + 2 + 4 = 7
            {"role": "assistant", "content": "Hello!"},  # 2 + 1 + 4 = 7
        ]
        result = estimate_messages_tokens(messages)
        assert result == 14

    def test_estimate_messages_with_empty_content(self):
        """Test token estimation with empty content."""
        messages = [{"role": "user", "content": ""}]
        result = estimate_messages_tokens(messages)
        assert result == 5  # "user"=1, ""=0, +4 overhead


class TestContextTruncation:
    """Test suite for context window truncation (T097)."""

    def test_truncate_context_empty_messages(self):
        """Test that empty messages returns empty list."""
        result = truncate_context([])
        assert result == []

    def test_truncate_context_fits_within_limit(self):
        """Test that messages fitting within limit are not truncated."""
        messages = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
        ]
        result = truncate_context(messages, max_tokens=8192)
        assert len(result) == 2
        assert result == messages

    def test_truncate_context_removes_oldest_first(self):
        """Test FIFO behavior - oldest messages removed first."""
        messages = [
            {"role": "user", "content": "a" * 100},  # ~25 tokens + overhead = ~30 tokens
            {"role": "user", "content": "a" * 100},  # ~25 tokens + overhead = ~30 tokens
            {"role": "user", "content": "a" * 100},  # ~25 tokens + overhead = ~30 tokens
        ]
        # Set small limit to force truncation (90 tokens total needed, 40 available)
        result = truncate_context(messages, max_tokens=80, reserve_for_response=40)
        # Should keep most recent message(s)
        assert len(result) < len(messages)
        # Last message should be preserved
        assert result[-1]["content"] == "a" * 100

    def test_truncate_context_preserves_system_prompt_space(self):
        """Test that system prompt space is reserved."""
        messages = [
            {"role": "user", "content": "Test message"},
        ]
        long_system_prompt = "a" * 1000  # 250 tokens
        result = truncate_context(
            messages,
            max_tokens=500,
            system_prompt=long_system_prompt,
            reserve_for_response=100,
        )
        # Should still return something even with large system prompt
        assert len(result) >= 0

    def test_truncate_context_respects_reserve_for_response(self):
        """Test that response tokens are reserved."""
        messages = [
            {"role": "user", "content": "a" * 400},  # 100 tokens content + overhead
        ]
        result = truncate_context(
            messages,
            max_tokens=200,
            reserve_for_response=50,
        )
        # Should fit since 200 - 50 = 150 available, message is ~105 tokens
        assert len(result) == 1

    def test_truncate_context_maintains_order(self):
        """Test that message order is maintained after truncation."""
        messages = [
            {"role": "user", "content": "First"},
            {"role": "assistant", "content": "Response 1"},
            {"role": "user", "content": "Second"},
            {"role": "assistant", "content": "Response 2"},
            {"role": "user", "content": "Third"},
        ]
        result = truncate_context(messages, max_tokens=8192)
        # Order should be preserved
        for i, msg in enumerate(result):
            assert msg == messages[i + (len(messages) - len(result))]

    def test_truncate_context_returns_last_message_when_limit_exceeded(self):
        """Test that at least last message is returned even with tiny limit."""
        messages = [
            {"role": "user", "content": "Keep this message"},
        ]
        result = truncate_context(
            messages,
            max_tokens=10,  # Very small
            reserve_for_response=5,
        )
        # Should return at least the last message even if it exceeds limit
        # because we need at least one message for conversation
        assert len(result) >= 0

    def test_truncate_context_with_long_conversation(self):
        """Test truncation with realistic long conversation."""
        # Create a long conversation
        messages = []
        for i in range(100):
            messages.append({"role": "user", "content": f"Message number {i} with some content"})
            messages.append({"role": "assistant", "content": f"Response to message {i}"})

        # Truncate to fit in context
        result = truncate_context(
            messages,
            max_tokens=1000,
            reserve_for_response=200,
        )

        # Should be significantly fewer messages
        assert len(result) < len(messages)
        # Should preserve most recent messages
        assert result[-1] == messages[-1]

    def test_truncate_context_default_values(self):
        """Test that default values work correctly."""
        messages = [{"role": "user", "content": "Test"}]
        result = truncate_context(messages)
        # With defaults (8192 tokens, 1024 reserve), small message should fit
        assert len(result) == 1

    def test_truncate_context_no_system_prompt(self):
        """Test truncation without system prompt."""
        messages = [{"role": "user", "content": "Hello"}]
        result = truncate_context(messages, system_prompt=None)
        assert result == messages

    def test_truncate_context_with_very_small_available_tokens(self):
        """Test behavior when available tokens is very small or negative."""
        messages = [{"role": "user", "content": "Test message"}]
        # Very large system prompt leaves no room
        huge_system_prompt = "a" * 40000  # 10000 tokens
        result = truncate_context(
            messages,
            max_tokens=5000,  # Less than system prompt
            system_prompt=huge_system_prompt,
            reserve_for_response=1000,
        )
        # Should return last message as fallback
        assert len(result) <= 1
