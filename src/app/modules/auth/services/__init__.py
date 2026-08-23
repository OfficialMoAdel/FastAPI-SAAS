"""Auth service collaborators.

Each module here owns one change-axis of authentication, extracted from the
``AuthService`` facade (which preserves the original public API):

- :mod:`.lockout` — brute-force / account-lockout policy (Redis-backed).
- :mod:`.tokens` — token claim building and access/refresh token issuance.
- :mod:`.passwords` — password change and reset lifecycle.
- :mod:`.email_verification` — email verification send/confirm flow.
"""
