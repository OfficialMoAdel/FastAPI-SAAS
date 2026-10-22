# AI Module - Agent Guidance

## Package Identity

AI chat service with Ollama integration for LLM interactions. Manages chat sessions, messages, user AI settings, and provides both standard and streaming completions. Includes rate limiting and context window management.

## Setup & Run

```bash
# Run AI unit tests
uv run pytest tests/unit/modules/ai/ -v

# Run AI integration tests
uv run pytest tests/integration/modules/ai/ -v

# Test chat completion
curl -X POST http://localhost:8000/api/v1/ai/sessions/1/chat \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello, how are you?"}'

# Test available models
curl http://localhost:8000/api/v1/ai/models \
  -H "Authorization: Bearer <token>"
```

## Patterns & Conventions

### DO: Use services for AI operations

```python
from src.app.modules.ai.service import (
    get_chat_session_service,
    get_chat_completion_service,
    get_ai_settings_service,
)

session_service = get_chat_session_service(db)
session = await session_service.create_session(user_id, ChatSessionCreate(title="New Chat"))

completion_service = get_chat_completion_service(db)
response = await completion_service.complete(session_id, user_id, request)
```

### DON'T: Call Ollama directly from routers

```python
# BAD - Ollama calls in router
@router.post("/chat")
async def chat(...):
    client = OllamaClient()
    await client.chat(...)  # Don't do this
```

### DO: Handle streaming responses properly

```python
from fastapi.responses import StreamingResponse

@router.post("/chat/stream")
async def chat_stream(...) -> StreamingResponse:
    async def generate():
        async for chunk in service.complete_stream(session_id, user_id, request):
            yield f"data: {json.dumps(chunk)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
```

### DO: Use rate limiting middleware

```python
from src.app.api.deps.ai_rate_limit import RateLimitedUser

@router.post("/chat")
async def chat_completion(
    current_user: RateLimitedUser,  # Rate-limited dependency
    ...
):
    ...
```

### DO: Handle AI exceptions properly

```python
from src.app.modules.ai.service import ChatCompletionError
from src.app.modules.ai.unified_errors import AIRateLimitError
from src.app.shared.exceptions import ServiceUnavailableError

try:
    response = await completion_service.complete(session_id, user_id, request)
except ChatCompletionError as e:
    # Domain error — the central EXCEPTION_HANDLERS table maps it to 503.
    raise ServiceUnavailableError(e.message) from e
```

### DO: Use user settings for defaults

```python
settings_service = get_ai_settings_service(db)
user_settings = await settings_service.get_or_create_settings(user_id)

# Use settings as defaults
model = request.model or user_settings.get("model", "gemma3")
temperature = request.temperature or user_settings.get("temperature", 0.7)
```

## Module Structure

```
src/app/modules/ai/
├── __init__.py              # Module facade (lazy __getattr__ re-exports)
├── router.py                # /ai endpoints
├── service.py               # Service classes
├── crud.py                  # CRUD operations
├── models.py                # SQLAlchemy models
├── schemas.py               # Pydantic schemas
├── providers.py             # AIProvider protocol + domain DTOs + errors
├── openai_provider.py       # OpenAI adapter (raw httpx)
├── anthropic_provider.py    # Anthropic adapter (raw httpx)
├── ollama_provider.py       # Ollama adapter (raw httpx)
├── factory.py               # AIProviderFactory (name -> adapter)
├── token_usage.py           # Token accounting
└── unified_errors.py        # AI-specific exceptions
```

`middleware.py` and `ollama_client.py` **no longer exist here**. The rate-limit
adapter moved to `src/app/api/deps/ai_rate_limit.py` (a FastAPI dependency, so
it belongs in the API layer); `OllamaClient` became `ollama_provider.py`.

Provider placement: `providers.py` and the three `*_provider.py` adapters stay
**inside this module** rather than moving to `src/app/clients/`. The rationale,
and why that is not the same leak as Stripe, is in
[ADR-0007](../../../../docs/adr/0007-inbound-stripe-boundary-and-ai-provider-placement.md).
Short version: they speak plain `httpx`, not a vendor SDK, and `providers.py` is
domain vocabulary the service layer constructs (`ChatMessage`) and consumes, so
relocating it would make the module depend on the adapter instead of the reverse.

## Touch Points

- **Session service**: `modules/ai/service.py::ChatSessionService`
- **Completion service**: `modules/ai/service.py::ChatCompletionService`
- **Settings service**: `modules/ai/service.py::AISettingsService`
- **Provider protocol/DTOs**: `modules/ai/providers.py::AIProvider`, `ChatMessage`, `ChatCompletionResponse`
- **Provider factory**: `modules/ai/factory.py::AIProviderFactory`
- **Ollama adapter**: `modules/ai/ollama_provider.py::OllamaProvider`
- **Rate limiting**: `core/security/budgets.py::probe` (`ai_chat` budget), adapted by `src/app/api/deps/ai_rate_limit.py`

## JIT Index Hints

```bash
# Find AI endpoints
rg -n "router\.(post|get|patch|delete)" src/app/modules/ai/router.py

# Find Ollama integration
rg -n "OllamaClient|ollama" src/app/modules/ai/

# Find service methods
rg -n "async def (complete|create_session|get_settings)" src/app/modules/ai/

# Find rate limiting
rg -n "RateLimiter|rate_limit" src/app/modules/ai/
```

## Common Gotchas

- **Token estimation**: Use 4 chars per token for rough estimation
- **Context window**: Manage context size to avoid truncation
- **Rate limiting**: 60 requests per minute per user by default
- **Streaming**: SSE format with proper Content-Type header
- **Model availability**: Check Ollama status before completions
- **Default model**: "gemma3" is the default model

## Default AI Settings

- **Model**: gemma3
- **Temperature**: 0.7 (range: 0.0-2.0)
- **Max tokens**: 2048 (range: 1-32000)

## Environment Variables

```bash
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_TIMEOUT=120
```

Both are read from `settings.ai` (`AISettings` in
`src/app/core/config/integrations.py`). `OllamaProvider` resolves BOTH its
default `base_url` and its default `timeout` from that setting, so a
deployment that points Ollama elsewhere is reachable without every caller
repeating the URL — the constructor's default is `None`, meaning "use the
configured one", not a hardcoded literal.

## Gotchas

- **A JSON parse failure logs the model body, capped**: `_extract_json` logs
  the offending body on a parse failure (`providers.py`). The body is capped
  at `MAX_LOGGED_BODY_CHARS` (2000) with the original length reported as
  `content_length`. The cap matters because the log sanitizer masks
  SECRET-SHAPED values (JWTs, API keys, emails, cards) but not free-form
  prose — and a parse failure means the model answered with prose, usually
  echoing the caller's prompt.

## Pre-PR Checks

```bash
uv run pytest tests/unit/modules/ai/ -v && \
uv run ruff check src/app/modules/ai/ && \
uv run mypy src/app/modules/ai/
```