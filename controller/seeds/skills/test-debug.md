---
name: Test Debugging
description: Use when investigating flaky tests, test failures, or test isolation problems
---

# Test Debugging

## When to Use
- A test passes locally but fails in CI (or vice versa)
- Tests fail intermittently (flaky tests)
- Test failures produce confusing or unhelpful error messages
- Tests interfere with each other (ordering-dependent failures)

## Instructions

1. **Reproduce the failure**:
   - Run the specific failing test in isolation: `pytest tests/test_foo.py::test_bar -v`
   - Run it multiple times to detect intermittent failures: `pytest --count=10 tests/test_foo.py::test_bar`
   - Run with verbose logging to capture more context
   - Check if it fails only when run with other tests (isolation issue)

2. **Diagnose flaky tests** -- common causes:
   - **Timing dependencies**: test relies on `time.sleep()`, timeouts, or race conditions. Fix: use deterministic waits (poll for condition) or mock time.
   - **Shared state**: tests share a database, file, or global variable. Fix: isolate each test with fresh state in setup/teardown.
   - **Non-deterministic data**: tests use `random`, `uuid`, or current time without seeding. Fix: use fixed seeds or deterministic factories.
   - **External dependencies**: tests call real APIs, services, or network. Fix: mock external calls or use recorded responses (VCR pattern).
   - **Resource exhaustion**: file descriptor leaks, connection pool exhaustion. Fix: ensure cleanup in teardown/finally blocks.

3. **Fix isolation problems**:
   - Each test must create and tear down its own state
   - Use transaction rollback for database tests (wrap each test in a transaction, roll back after)
   - Reset singletons and module-level caches between tests
   - Check for test pollution: does the test modify `os.environ`, `sys.path`, or class attributes?

4. **Improve error messages**:
   - Add custom assertion messages: `assert result == expected, f"Expected {expected} but got {result} for input {input}"`
   - Log the actual state when an assertion fails (dump the object, not just a boolean)
   - For async tests, capture and log any background exceptions

## Checklist
- [ ] Failure is reproducible (or identified as flaky with root cause)
- [ ] Test runs in isolation without depending on other tests
- [ ] No timing dependencies (no bare sleep, no race conditions)
- [ ] External dependencies are mocked or recorded
- [ ] Shared state is properly isolated (database, globals, env vars)
- [ ] Assertion messages include actual vs expected values
