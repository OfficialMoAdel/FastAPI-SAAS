# ADR-0007: Inbound Stripe boundary (alias the event type) and AI providers stay put

**Status:** Accepted
**Date:** 2026-09-30
**Context:** A review claimed one boundary gap with two halves: "Outbound integrations
(Stripe, SMTP, storage) live behind `src/app/clients/`" is *not* enforced on the
inbound side, because `import stripe` appeared in 6 files under
`src/app/modules/billing/webhook/`, and "the `ai` module has no adapter at all".

Those two halves look like one finding and must not be closed with one action.
The first is a real leak with a small correct fix. The second is, on inspection, not
the same defect — and acting on it as written would have damaged a working design.

## Decision

**1. Inbound Stripe: alias the event type behind one client import. (Act.)**

`clients/billing/stripe.py` gains one alias:

```python
StripeEvent = stripe.Event
```

re-exported from `src.app.clients.billing`. The six webhook modules — `client.py`,
`dispatch.py`, and the four handlers — spell the annotation as `StripeEvent` and no
longer import the SDK. Two of them (`dispatch.py`, and every handler) were importing
the vendor SDK for a *type annotation alone*: `stripe.Event` was never called on.

This is a pass-by-reference alias, not a Protocol or a wrapper, so `isinstance`
checks, Stripe's own attribute access, and runtime behaviour are identical.

**2. `webhook/client.py` keeps its `import stripe`. (Deliberate, narrowed.)**

`verify_webhook_signature` is not an outbound adapter — it *is* the trust boundary
that authenticates the wire. It must call `stripe.Webhook.construct_event` and must
catch `stripe.SignatureVerificationError`, a class that exists nowhere else. The
boundary rule is therefore stated positively: **exactly two modules may
`import stripe`** — `clients/billing/stripe.py` (outbound) and
`billing/webhook/client.py` (inbound verification). Both are named in the
ratchet test, so the set is pinned rather than assumed.

**3. AI providers: amend the doc, do NOT move them. (Decided against a move.)**

`providers.py` and the three `*_provider.py` adapters **stay in
`src/app/modules/ai/`.** Evidence, in order of weight:

1. **There is no vendor SDK to hide.** `openai` and `anthropic` are declared
   dependencies in `pyproject.toml` and imported **nowhere** in the repo (verified:
   zero `import openai` / `import anthropic` in `src/`, `tests/`, `scripts/`). The
   adapters speak plain `httpx`. The premise "ai has no adapter" rests on a package
   existing; there is no `openai.*` or `anthropic.*` symbol anywhere in `ai/`. The
   thing that would leak across the boundary does not exist.
2. **`providers.py` is domain vocabulary, not an adapter.** `ChatMessage`,
   `ChatCompletionResponse`, `AITokenUsage`, `EmbeddingResponse` and `AIProviderError`
   are constructed and consumed by the service layer — `service.py` builds
   `ChatMessage(**m)` and reads `res.token_usage`, and `unified_errors.py` derives
   from `AIProviderError`. Relocating that file to `clients/` would make the feature
   module import its own vocabulary from an adapter package: the dependency inverts,
   and the "adapter" would be a leaf imported by everyone it is supposed to serve.
   A seam is defined by what depends on what, not by where the file sits.
3. **`httpx` outbound code already lives beside its feature** in two other places —
   `modules/auth/http_client.py` and `modules/webhooks/http_client.py`. Moving the
   AI adapters alone would establish no precedent and fix no inconsistency.
4. **The cost is real and the benefit is not.** A move relocates ~850 lines across a
   package boundary that ten call sites in `src/` and a dozen-plus white-box tests
   reach, for zero vendor-SDK decoupling.

So `AGENTS.md` is **amended** to state what is actually true, rather than the module
being moved to match prose.

## Rationale

The Stripe half is worth fixing precisely because it is cheap and the fix is
semantically real: importing a vendor SDK to spell a type annotation gives every
other module in the package the right to reach the whole SDK, and no gate can tell
that apart from a legitimate call. Aliasing at the adapter keeps the SDK reachable
from exactly one name.

The AI half is worth *not* fixing because the two findings share a sentence and
almost nothing else. A boundary rule earns its keep by tracking what actually
crosses it; applying it to a package that never imports a vendor SDK would spend a
large refactor to relocate a false positive. The honest outcome is that the review's
phrase "ai has no adapter at all" is **retracted on its own terms** — the module
needs no adapter because it depends on no SDK.

The remaining real inconsistency is that `openai` and `anthropic` are declared
dependencies that nothing imports. That is dependency debt, not a layering
violation, and is deliberately left for a dependency-audit pass rather than folded
in here — removing them would change `uv.lock` and needs its own verification.

## Consequences

- `import stripe` in `src/` is down from 7 modules to 2, both named in
  `tests/core/test_stripe_adapter_boundary.py`, which fails if a third appears or if
  a non-owner names `stripe.Event`.
- The webhook pipeline's handler signatures are unchanged at runtime; the six
  annotations now resolve through the alias (verified: `StripeEvent is stripe.Event`).
- `AGENTS.md` and `src/app/modules/ai/AGENTS.md` state the real boundary. The AI
  module doc also had two dead pointers (`middleware.py`, `ollama_client.py`, both
  moved/renamed earlier) and a file list missing 4 of 12 files; both are corrected
  here, since the doc is what the "amend" branch has to leave accurate.
