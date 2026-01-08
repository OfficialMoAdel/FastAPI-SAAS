# Feature Specification: Billing & Payments and AI Features Modules

**Feature Branch**: `008-billing-ai-modules`
**Created**: 2026-01-08
**Status**: Draft
**Input**: User description: "Use @IMPLEMENTATION_WORKFLOW.md and @MIGRATION_PLAN.md as the source of truth for scope, dependencies, and module structure. Write a spec for only the Phase 3, 5 modules, Keeping in mind that there is a modular _base that can be used to serve this"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Tenant Administrator Configures Stripe Integration (Priority: P1)

A tenant administrator needs to connect their Stripe account to enable payment processing for their SaaS application. This is the foundational step that unlocks all billing capabilities.

**Why this priority**: Without Stripe integration, no payment processing is possible. This is the prerequisite for all billing functionality.

**Independent Test**: Can be fully tested by configuring Stripe credentials and verifying webhook connectivity, delivering the ability to process payments.

**Acceptance Scenarios**:

1. **Given** a tenant administrator is logged in and has MANAGE_BILLING permission, **When** they provide valid Stripe API credentials, **Then** the system stores the credentials securely and marks the integration as active.
2. **Given** Stripe integration is configured, **When** a Stripe webhook event arrives, **Then** the system validates the webhook signature and processes the event.
3. **Given** invalid Stripe credentials are provided, **When** the administrator saves the configuration, **Then** the system displays a validation error and does not save invalid credentials.

---

### User Story 2 - Admin Creates and Manages Subscription Plans (Priority: P1)

A tenant administrator needs to create subscription plans with different pricing tiers (monthly/yearly) and feature sets so customers can choose the plan that fits their needs.

**Why this priority**: Subscription plans are essential for monetization and must exist before customers can subscribe.

**Independent Test**: Can be fully tested by creating, updating, and deleting subscription plans, delivering the ability to offer tiered pricing.

**Acceptance Scenarios**:

1. **Given** a tenant administrator with MANAGE_PRODUCTS_AND_PRICING permission, **When** they create a new subscription plan with name, description, monthly price, yearly price, and features, **Then** the plan is saved and appears in the plans list.
2. **Given** multiple subscription plans exist, **When** viewing the plans list, **Then** plans are displayed in sort order with their pricing and feature details.
3. **Given** a subscription plan is marked as popular, **When** customers view available plans, **Then** the popular plan is visually distinguished.
4. **Given** a subscription plan exists, **When** the administrator deactivates it, **Then** new subscriptions cannot be created for that plan but existing subscriptions remain active.

---

### User Story 3 - Customer Completes Checkout and Subscribes (Priority: P1)

A customer selects a subscription plan and completes the checkout process to become a paying subscriber.

**Why this priority**: This is the core revenue-generating user flow that directly impacts business viability.

**Independent Test**: Can be fully tested by selecting a plan and completing payment, delivering active subscription status.

**Acceptance Scenarios**:

1. **Given** a customer is viewing available subscription plans, **When** they select a plan and click checkout, **Then** they are redirected to a secure payment page.
2. **Given** a customer completes payment successfully, **When** the payment is confirmed, **Then** a billing record is created and the customer's subscription is activated.
3. **Given** a payment fails (declined card, insufficient funds), **When** the payment processor returns an error, **Then** the customer sees a clear error message and can retry with different payment details.
4. **Given** a customer has an active subscription, **When** they view their account, **Then** they can see their current plan and billing status.

---

### User Story 4 - Customer Views Billing History and Invoices (Priority: P2)

A subscribed customer needs to view their billing history and download invoices for expense reporting or record-keeping.

**Why this priority**: Important for customer self-service but not required for core payment flow.

**Independent Test**: Can be fully tested by viewing billing records and downloading invoice documents.

**Acceptance Scenarios**:

1. **Given** a customer has billing records, **When** they access their billing history, **Then** they see a paginated list of transactions with date, amount, status, and description.
2. **Given** a billing record exists, **When** the customer requests an invoice, **Then** they can download or view the invoice document.
3. **Given** no billing records exist, **When** the customer accesses billing history, **Then** they see an appropriate empty state message.

---

### User Story 5 - Customer Cancels Subscription (Priority: P2)

A customer decides to cancel their subscription and needs a clear process to do so.

**Why this priority**: Required for customer autonomy and compliance but secondary to subscription creation.

**Independent Test**: Can be fully tested by initiating cancellation and verifying subscription status change.

**Acceptance Scenarios**:

1. **Given** a customer has an active subscription, **When** they request cancellation, **Then** they are shown the end date of their current billing period.
2. **Given** a customer confirms cancellation, **When** the cancellation is processed, **Then** the subscription is marked for non-renewal and the customer retains access until the billing period ends.
3. **Given** a subscription is cancelled, **When** the billing period ends, **Then** the customer's access is appropriately adjusted.

---

### User Story 6 - Admin Creates Products with Pricing Tiers (Priority: P2)

A tenant administrator needs to create products with multiple pricing tiers for one-time purchases or add-on services beyond subscriptions.

**Why this priority**: Extends monetization options but not required for basic subscription billing.

**Independent Test**: Can be fully tested by creating products and associating pricing tiers.

**Acceptance Scenarios**:

1. **Given** an administrator with MANAGE_PRODUCTS_AND_PRICING permission, **When** they create a product with name and description, **Then** the product is saved and can have pricing tiers attached.
2. **Given** a product exists, **When** the administrator adds a pricing tier with name, price, and interval (monthly/yearly/one_time), **Then** the pricing tier is associated with the product.
3. **Given** a product has multiple pricing tiers, **When** viewing the product, **Then** all associated pricing tiers are displayed with their features.

---

### User Story 7 - User Creates AI Chat Session (Priority: P1)

A user wants to start a new AI conversation to get assistance with their tasks within the application.

**Why this priority**: This is the primary entry point for AI functionality and delivers immediate user value.

**Independent Test**: Can be fully tested by creating a chat session and sending a message, delivering AI-powered assistance.

**Acceptance Scenarios**:

1. **Given** an authenticated user, **When** they create a new chat session, **Then** a session is created with a unique identifier and optional title.
2. **Given** a chat session exists, **When** the user sends a message, **Then** the message is stored and an AI-generated response is returned.
3. **Given** an active chat session, **When** viewing the session, **Then** the full message history is displayed in chronological order.

---

### User Story 8 - User Manages Multiple AI Chat Sessions (Priority: P2)

A user needs to organize and access their previous AI conversations for reference or continuation.

**Why this priority**: Improves user experience but a single session is sufficient for basic AI interaction.

**Independent Test**: Can be fully tested by listing, viewing, and deleting chat sessions.

**Acceptance Scenarios**:

1. **Given** a user has multiple chat sessions, **When** they access their sessions list, **Then** they see all sessions with titles and last activity timestamps.
2. **Given** multiple sessions exist, **When** the user selects a session, **Then** the full conversation history is loaded.
3. **Given** a chat session exists, **When** the user deletes the session, **Then** the session and all messages are permanently removed.

---

### User Story 9 - User Configures AI Preferences (Priority: P3)

A user wants to customize their AI experience by selecting preferred models and adjusting response characteristics.

**Why this priority**: Enhances personalization but default settings provide functional AI experience.

**Independent Test**: Can be fully tested by modifying AI settings and observing different AI behavior.

**Acceptance Scenarios**:

1. **Given** an authenticated user, **When** they access AI settings, **Then** they see available configuration options including model selection.
2. **Given** the user modifies AI settings (model, temperature, max tokens), **When** they save changes, **Then** subsequent AI interactions use the new settings.
3. **Given** available AI models exist, **When** the user views model options, **Then** they see a list of available models to choose from.
4. **Given** the user sets a custom system prompt, **When** starting a new chat session, **Then** the AI uses the custom system prompt.

---

### Edge Cases

**Billing Edge Cases**
- **Webhook retry handling**: System processes webhooks idempotently using event ID; duplicate events are acknowledged but not reprocessed
- **Mid-cycle plan changes**: Proration is handled by Stripe; system syncs updated subscription via webhook
- **Payment currency mismatch**: All amounts stored in cents with original currency; no automatic conversion performed
- **Refund processing**: System handles charge.refunded webhook and updates billing record status; no in-app refund initiation
- **Checkout session expiry**: Sessions expire after 24 hours; user must create new checkout session

**AI Edge Cases**
- **AI service unavailable**: System returns immediate HTTP 503 error with clear message; user's message is not stored until AI responds successfully; user must manually retry
- **Token limit handling**: System automatically truncates oldest messages (FIFO) from context window when approaching limit, while preserving the system prompt; truncation is silent; all messages remain stored in database for history retrieval
- **Concurrent messages in session**: System processes messages sequentially per session to maintain ordering; concurrent requests are queued
- **User deletion cascade**: Chat sessions and messages are cascade deleted when user is deleted
- **Model not available**: If requested model is not available in Ollama, system returns HTTP 503 with specific error message

## Requirements *(mandatory)*

### Functional Requirements

#### Billing & Payments (Phase 3)

**Stripe Integration**
- **FR-001**: System MUST allow tenant administrators to configure Stripe API credentials (secret key, webhook secret) securely
- **FR-001a**: System MUST encrypt Stripe API keys at rest using Fernet symmetric encryption
- **FR-001b**: System MUST NOT expose Stripe credentials in logs, API responses, or error messages
- **FR-002**: System MUST validate Stripe webhook signatures using HMAC-SHA256 to prevent spoofed events
- **FR-003**: System MUST handle Stripe webhook events for payment confirmations, failures, and subscription updates
- **FR-003a**: System MUST process webhooks idempotently using Stripe event ID to prevent duplicate processing
- **FR-003b**: System MUST return HTTP 200 within 5 seconds to avoid Stripe retry attempts
- **FR-003c**: System MUST handle out-of-order webhook delivery gracefully (e.g., subscription.updated before checkout.session.completed)
- **FR-003d**: System MUST process webhooks in background tasks after signature validation
- **FR-004**: System MUST store Stripe customer IDs to link platform users with Stripe customers
- **FR-005**: System MUST require MANAGE_BILLING permission for Stripe settings management

**Subscription Plans**
- **FR-006**: System MUST allow creation of subscription plans with name, description, and pricing
- **FR-007**: System MUST support both monthly and yearly pricing for subscription plans
- **FR-008**: System MUST store subscription plan features as a structured list
- **FR-009**: System MUST support marking plans as popular or featured
- **FR-010**: System MUST support sort ordering for plan display
- **FR-011**: System MUST allow activation/deactivation of subscription plans
- **FR-012**: System MUST isolate subscription plans by tenant

**Products & Pricing**
- **FR-013**: System MUST allow creation of products with name and description
- **FR-014**: System MUST support multiple pricing tiers per product
- **FR-015**: System MUST support monthly, yearly, and one-time pricing intervals
- **FR-016**: System MUST store pricing tier features as a structured list
- **FR-017**: System MUST isolate products by tenant

**Billing Operations**
- **FR-018**: System MUST create billing records for all payment transactions
- **FR-019**: System MUST store billing records with amount, currency, status (succeeded/failed/pending/refunded), and payment references
- **FR-019a**: Payment errors MUST return HTTP 402 with body containing detail message and Stripe error code
- **FR-020**: System MUST support paginated billing history retrieval
- **FR-021**: System MUST provide checkout session creation for payment initiation
- **FR-021a**: Checkout sessions MUST expire after 24 hours if not completed
- **FR-022**: System MUST support subscription cancellation with end-of-period access
- **FR-022a**: System MUST track user subscription status with lifecycle states (active, canceled, past_due, expired, trialing)
- **FR-022b**: System MUST update user subscription status based on Stripe webhook events
- **FR-022c**: System MUST handle charge.refunded webhook and update billing record status to "refunded"

**Subscription Lifecycle State Transitions**
- **FR-022d**: `active → canceled`: Triggered when user initiates cancellation (sets cancel_at_period_end=true)
- **FR-022e**: `active → past_due`: Triggered by invoice.payment_failed webhook
- **FR-022f**: `past_due → active`: Triggered by invoice.payment_succeeded webhook after retry
- **FR-022g**: `past_due → expired`: Triggered after 3 consecutive failed payment attempts
- **FR-022h**: `canceled → expired`: Triggered when current_period_end is reached
- **FR-022i**: `trialing → active`: Triggered when trial ends and first payment succeeds
- **FR-022j**: `trialing → expired`: Triggered when trial ends and payment fails

#### AI Features (Phase 5)

**AI Chat Sessions**
- **FR-023**: System MUST allow authenticated users to create chat sessions
- **FR-024**: System MUST store chat sessions with user and tenant associations
- **FR-025**: System MUST support optional titles for chat sessions
- **FR-026**: System MUST support listing user's chat sessions with pagination
- **FR-027**: System MUST support retrieving individual chat sessions
- **FR-028**: System MUST support deleting chat sessions with cascading message deletion

**AI Chat Messages**
- **FR-029**: System MUST store chat messages with role (user/assistant/system) and content
- **FR-030**: System MUST track token count for AI responses (estimated at 4 characters per token)
- **FR-031**: System MUST maintain message ordering within sessions
- **FR-032**: System MUST return AI-generated responses when users send messages
- **FR-032a**: AI service errors MUST return HTTP 503 with body: {"detail": "<message>", "retry_after": <seconds|null>}
- **FR-032b**: User message MUST NOT be stored until AI response succeeds; both are saved atomically
- **FR-033**: System MUST retrieve message history for a session
- **FR-033a**: System MUST automatically truncate oldest messages from AI context window when token limit is approached, while preserving the system prompt
- **FR-033b**: System MUST use model-specific context windows (default: 4096 tokens for gemma3, 8192 for llama3)
- **FR-033c**: System MUST reserve 25% of context window for AI response generation
- **FR-033d**: System MUST NOT notify user when context truncation occurs (silent operation)
- **FR-033e**: All messages MUST remain stored in database regardless of context truncation

**AI Settings**
- **FR-034**: System MUST allow users to configure AI model preferences
- **FR-035**: System MUST support temperature (0.0-2.0, default: 0.7) and max token (1-32000, default: 2048) configuration
- **FR-036**: System MUST support custom system prompts per user
- **FR-037**: System MUST persist AI preferences across sessions
- **FR-038**: System MUST provide list of available AI models dynamically from Ollama
- **FR-038a**: Default AI settings when user has no preferences: model="gemma3", temperature=0.7, max_tokens=2048, system_prompt=null

**AI Rate Limiting**
- **FR-039**: System MUST enforce rate limit of 60 AI requests per user per minute
- **FR-040**: System MUST enforce maximum of 100 concurrent AI sessions per tenant
- **FR-041**: Rate limit exceeded errors MUST return HTTP 429 with Retry-After header

### Key Entities

**Billing & Payments**

- **StripeSettings**: Tenant-level Stripe configuration including encrypted API keys (secret_key, webhook_secret), customer ID, and activation status. One per tenant.
- **BillingRecord**: Individual payment transaction record with amount (in cents), currency (ISO 4217), status (succeeded/failed/pending/refunded), Stripe payment intent ID, and description. Linked to user and tenant.
- **SubscriptionPlan**: Pricing plan definition with monthly/yearly pricing (in cents), feature list (JSONB array), sort order, and popularity flag. Belongs to a tenant.
- **Product**: Purchasable item with name and description. Can have multiple pricing tiers. Belongs to a tenant.
- **Pricing**: Price tier for a product with amount (in cents), currency, billing interval (monthly/yearly/one_time), and feature list. Linked to a product.
- **UserSubscription**: User's active subscription linking to a plan. Includes subscription status (active/canceled/past_due/expired/trialing), billing interval (monthly/yearly), current_period_start, current_period_end, cancel_at_period_end flag, Stripe subscription ID, and cancellation timestamp. One active subscription per user per tenant.

**AI Features**

- **AIChatSession**: Conversation container with optional title and model selection (defaults to user's preferred model or "gemma3"). Linked to user and tenant. Contains multiple messages.
- **AIChatMessage**: Individual message in a conversation with role (user/assistant/system), content text, and token count (nullable for user messages). Linked to a session. Ordered by created_at.
- **AISettings**: User-level AI preferences including selected model (default: "gemma3"), temperature (default: 0.7), max_tokens (default: 2048), and custom system prompt (nullable). One per user.

## Success Criteria *(mandatory)*

### Measurable Outcomes

**Billing & Payments**
- **SC-001**: Tenant administrators can configure Stripe integration in under 5 minutes
- **SC-002**: Checkout flow completes in under 30 seconds for 95% of transactions
- **SC-003**: Billing history loads and displays within 2 seconds for users with up to 1000 transactions
- **SC-004**: System processes 99% of Stripe webhooks successfully on first delivery attempt
- **SC-005**: Subscription plan changes take effect within 5 seconds of admin action

**AI Features**
- **SC-006**: Users can create a new chat session and receive first AI response in under 5 seconds
- **SC-007**: Chat message history loads within 2 seconds for sessions with up to 500 messages
- **SC-008**: AI settings changes are reflected in the next user request without requiring re-login
- **SC-009**: System supports 100 concurrent AI chat sessions per tenant without degradation
- **SC-010**: 95% of users can configure their AI preferences on first attempt

## Clarifications

### Session 2026-01-08

- Q: How is a user's active subscription status tracked (which plan, expiry, status)? → A: Create a `UserSubscription` entity linking user to plan with status and billing dates
- Q: What happens when AI model service is temporarily unavailable? → A: Fail immediately with error message, user must manually retry
- Q: How are very long AI conversations handled (token limits)? → A: Automatically truncate oldest messages from context window, keeping system prompt

## Assumptions

1. **Stripe is the sole payment processor** - No support for other payment gateways is required in this phase
2. **Ollama is the primary AI provider** - Initial implementation targets Ollama with potential OpenAI expansion later
3. **Existing RBAC permissions** - MANAGE_BILLING and MANAGE_PRODUCTS_AND_PRICING permissions exist in the permission enum
4. **Tenant isolation** - All billing and AI data inherits the application's existing multi-tenancy patterns
5. **Currency handling** - Amount storage in cents with currency code; currency conversion is out of scope
6. **AI model availability** - At least one AI model (gemma3) is available when AI features are used
7. **_base module usage** - All new modules inherit from existing BaseService, BaseCreate, BaseRead, BaseUpdate patterns
8. **User authentication** - All endpoints require authenticated users via existing JWT authentication
9. **PCI DSS compliance** - Card data is handled entirely by Stripe; no card details touch our servers
10. **SDK versions** - stripe>=8.0.0, ollama>=0.3.0 (as specified in research.md)
11. **Refunds via Stripe** - Refunds are initiated directly in Stripe dashboard; system only tracks refund status via webhooks
