---
name: Test Writing Guide
description: Use when writing new tests or reviewing test quality and coverage
---

# Test Writing Guide

## When to Use
- Writing unit tests, integration tests, or end-to-end tests
- Reviewing test PRs for coverage and quality
- Designing test fixtures and test data strategies
- Improving flaky or poorly structured tests

## Instructions

1. **AAA pattern**: Structure every test as Arrange-Act-Assert:
   - **Arrange**: Set up preconditions, create test data, configure mocks
   - **Act**: Execute the single behavior being tested
   - **Assert**: Verify the expected outcome with specific assertions

2. **Test naming**: Names should describe the scenario and expected behavior:
   - Format: `test_[unit]_[scenario]_[expected_result]`
   - Example: `test_create_user_with_duplicate_email_returns_409`
   - Avoid generic names like `test_user` or `test_happy_path`

3. **Fixture design**:
   - Create factory functions or builders for test data (avoid raw object literals repeated across tests)
   - Use minimal data: only set fields relevant to the test
   - Isolate fixtures: each test should create its own state, never depend on other tests' data
   - Clean up side effects in teardown

4. **Edge cases to cover**:
   - Empty inputs (empty string, empty array, null/None)
   - Boundary values (0, -1, MAX_INT, empty page, last page)
   - Invalid types (string where number expected, if dynamically typed)
   - Concurrent access (if applicable)
   - Error paths (network failure, timeout, permission denied)

5. **Mocking strategy**:
   - Mock at the boundary (HTTP clients, database, file system), not internal functions
   - Verify mock interactions only when the interaction itself is the behavior being tested
   - Prefer fakes (in-memory database) over mocks for complex dependencies
   - Never mock the system under test

6. **Assertions**:
   - One logical assertion per test (multiple `assert` calls are fine if they verify one behavior)
   - Use specific assertions: `assertEqual(result, 42)` not `assertTrue(result == 42)`
   - Assert on the exact expected value, not just truthiness

## Checklist
- [ ] Every test follows Arrange-Act-Assert structure
- [ ] Test names describe scenario and expected result
- [ ] Test data created via factories, not duplicated literals
- [ ] Edge cases covered: empty, boundary, invalid, error paths
- [ ] Mocks used at boundaries only, not on internal functions
- [ ] Each test is independent and can run in isolation
