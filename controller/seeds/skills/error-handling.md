---
name: Error Handling Patterns
description: Use when implementing or reviewing error handling, retry logic, or fault tolerance
---

# Error Handling Patterns

## When to Use
- Implementing error handling for API calls, database operations, or external services
- Reviewing code for proper error propagation and recovery
- Adding retry logic or circuit breakers to unreliable dependencies
- Improving observability through structured error logging

## Instructions

1. **Structured errors**: Define error types with machine-readable codes and human-readable messages. Include context (request ID, resource ID, timestamp). Never expose internal details (stack traces, SQL queries) to API consumers.

2. **Error classification**:
   - **Retryable**: network timeouts, 503 Service Unavailable, connection reset, lock contention
   - **Non-retryable**: 400 Bad Request, 404 Not Found, validation errors, authentication failures
   - **Fatal**: out of memory, disk full, configuration errors -- alert and stop

3. **Retry strategy**: Use exponential backoff with jitter for retryable errors:
   - Start with 100-500ms delay
   - Multiply by 2 each attempt, add random jitter (0-50% of delay)
   - Cap at 3-5 retries with a maximum total timeout
   - Log each retry attempt with attempt number and delay

4. **Circuit breaker pattern**: When a dependency fails repeatedly:
   - **Closed** (normal): requests pass through, failures counted
   - **Open** (tripped): requests fail fast without calling dependency, checked periodically
   - **Half-open** (testing): allow one request through to test if dependency recovered
   - Trip after N failures in a time window; reset after a successful half-open probe

5. **Logging**: Log errors with severity levels. Include: error code, message, stack trace (server-side only), request context, and correlation ID. Use structured JSON logging for machine parsing.

6. **Graceful degradation**: When a non-critical dependency fails, serve a degraded response rather than a full error. Cache previous good responses as fallbacks.

## Checklist
- [ ] All errors have machine-readable codes and human-readable messages
- [ ] Retryable vs. non-retryable errors are classified
- [ ] Retry logic uses exponential backoff with jitter
- [ ] Circuit breakers protect against cascading failures
- [ ] Errors are logged with correlation IDs and context
- [ ] No internal details leaked to API consumers
- [ ] Graceful degradation for non-critical dependencies
