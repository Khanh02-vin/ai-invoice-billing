## Implementation plan

1. **Webhook normalization and idempotency**
   - Extend the provider-neutral webhook model to carry the provider’s stable event ID and a normalized nested resource/object payload.
   - Update `MockProvider` to read `event_id`/`id` from the provider envelope, preserve it unchanged, and reject missing/invalid payloads safely.
   - Update billing webhook application logic to consume `data.object` (with compatibility fallback for existing mock fixtures), extract nested metadata/user/org/plan/status fields, persist the actual provider subscription ID, and use the persisted event ID for duplicate detection.
   - Add route-level tests for nested create/update/delete events and replaying the same event ID.

2. **Canonical auth/user persistence and token lifecycle**
   - Keep `UserRepository` as the canonical persistence boundary used by both app auth and billing dependencies; remove billing’s independent repository behavior by wiring the billing router to the application repository or a shared repository factory.
   - Add refresh-token issuance/rotation at registration and login using the existing refresh-token table/helpers, while retaining access-token compatibility for current clients.
   - Add a centralized authenticated-user dependency that rejects unverified users with a stable `EMAIL_NOT_VERIFIED` error for protected routes, with an explicit exemption for verification/auth endpoints.
   - Ensure password reset/revocation continues to invalidate refresh tokens and add tests for unverified rejection, verified access, and refresh rotation.

3. **Global provider error normalization**
   - Preserve the existing global `AppError` response contract and request IDs.
   - Wrap provider failures in billing checkout, portal, webhook, and subscription calls with internal logging and a safe `PAYMENT_FAILED` response (`payment_failed`-compatible code, HTTP 502/503), never returning raw provider exception text.
   - Normalize unsupported providers and malformed provider responses through the same handler, while leaving validation/auth errors unchanged.
   - Add tests asserting safe response shape, status, request ID, and absence of internal exception details.

4. **Verification**
   - Run focused webhook/auth/error tests, the complete backend suite, Python compilation, frontend build, Docker/Compose checks, and whitespace/status review.
   - Report the foundation runner JSON with `status: completed` and all six `fired: true` entries, plus any remaining external-provider limitations.

## Scope note

The repository currently uses SQLite rather than SQLAlchemy/PostgreSQL. I will implement the requested single canonical user persistence boundary using the existing SQLite repository abstraction rather than introduce a second ORM/database stack solely for this change; the boundary can later be swapped for SQLAlchemy/PostgreSQL without changing router contracts.