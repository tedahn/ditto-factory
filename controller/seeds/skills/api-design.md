---
name: REST API Design
description: Use when designing, reviewing, or refactoring REST API endpoints
---

# REST API Design

## When to Use
- Creating new API endpoints or resources
- Reviewing API PRs for consistency and best practices
- Refactoring existing APIs for better developer experience

## Instructions

1. **Resource naming**:
   - Use plural nouns for collections: `/users`, `/orders`, `/products`
   - Use nested routes for relationships: `/users/{id}/orders`
   - Keep URLs lowercase with hyphens: `/order-items` not `/orderItems`
   - Limit nesting to 2 levels max; beyond that, use query parameters or top-level resources

2. **HTTP methods and status codes**:
   - GET (read), POST (create), PUT (full replace), PATCH (partial update), DELETE (remove)
   - 200 OK, 201 Created (POST), 204 No Content (DELETE), 400 Bad Request, 401 Unauthorized, 403 Forbidden, 404 Not Found, 409 Conflict, 422 Unprocessable Entity, 429 Too Many Requests, 500 Internal Server Error

3. **Error responses**: Use a consistent error envelope:
   ```json
   { "error": { "code": "VALIDATION_ERROR", "message": "Human-readable message", "details": [{"field": "email", "issue": "invalid format"}] } }
   ```

4. **Pagination**: Use cursor-based pagination for large datasets:
   - Request: `GET /items?cursor=abc123&limit=25`
   - Response: include `next_cursor` and `has_more` in metadata

5. **Versioning**: Prefer URL path versioning (`/v1/users`) for breaking changes. Use header-based versioning only if URL versioning is impractical.

6. **Filtering and sorting**:
   - Filter: `GET /products?category=electronics&min_price=10`
   - Sort: `GET /products?sort=price&order=asc`
   - Search: `GET /products?q=search+term`

## Checklist
- [ ] Resource names are plural nouns, lowercase with hyphens
- [ ] Correct HTTP methods used for each operation
- [ ] Status codes match the outcome accurately
- [ ] Error responses use a consistent envelope format
- [ ] Pagination implemented for list endpoints
- [ ] API version included in URL path
- [ ] Rate limiting headers included (X-RateLimit-*)
