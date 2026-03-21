---
name: General Code Review
description: Use when performing a general code review for quality, readability, and correctness
---

# General Code Review

## When to Use
- Reviewing any pull request for code quality
- Auditing existing code for maintainability issues
- Onboarding to a new codebase and assessing quality
- Refactoring code for clarity and correctness

## Instructions

1. **Readability first**:
   - Functions should do one thing and be under 40 lines
   - Variable and function names should reveal intent: `get_active_users()` not `getData()`
   - Avoid abbreviations unless universally understood (`id`, `url`, `db` are fine; `usr_mgr_svc` is not)
   - Comments explain "why," not "what" -- the code itself should explain what it does

2. **SOLID principles check**:
   - **Single Responsibility**: Does each class/module have one reason to change?
   - **Open/Closed**: Can behavior be extended without modifying existing code?
   - **Dependency Inversion**: Do high-level modules depend on abstractions, not concretions?
   - Skip Liskov and Interface Segregation unless the code uses inheritance or interfaces heavily

3. **Error handling**:
   - No bare `except:` or `catch(e) {}` that swallow errors silently
   - Error messages include context (what was being attempted, with what input)
   - Resources are cleaned up in `finally`/`defer`/`using` blocks
   - Errors at API boundaries return appropriate status codes

4. **Correctness concerns**:
   - Check for off-by-one errors in loops and slicing
   - Verify null/undefined handling at function boundaries
   - Look for race conditions in concurrent code (shared state without synchronization)
   - Validate that edge cases are handled (empty collections, zero values, max values)

5. **Performance red flags**:
   - N+1 queries (database call inside a loop)
   - Unbounded collection growth (no pagination, no limits)
   - Synchronous blocking calls in async code paths
   - Large objects copied unnecessarily

6. **Security scan**:
   - User input is validated and sanitized before use
   - SQL queries use parameterized statements, never string concatenation
   - Secrets are not hardcoded or logged
   - Authentication/authorization checks are present on protected endpoints

## Checklist
- [ ] Functions are focused, well-named, and under 40 lines
- [ ] No silent error swallowing; errors include context
- [ ] No N+1 queries or unbounded collection growth
- [ ] User input validated; SQL parameterized; no hardcoded secrets
- [ ] Edge cases handled (null, empty, boundary values)
- [ ] Code is testable (dependencies can be injected or mocked)
