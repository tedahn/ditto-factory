# Plan: MCP Gateway Real Tool Backends (v1)

## Status: Draft
**Date:** 2026-03-21
**Scope:** Replace placeholder handlers in `src/mcp/gateway/server.js` with working v1 implementations.

---

## 1. Tool Specifications (v1 Scope)

### 1.1 file-analysis (analyze_file)

**What it does:** Reads a file from a configurable base directory and returns structural metadata. No AST parsing in v1 — just file-system-level analysis.

**v1 capabilities by `analysis_type`:**
| Type | Output |
|------|--------|
| `structure` | Line count, file size, extension, encoding detection, top-level section headers (for code: function/class names via regex) |
| `dependencies` | For JS/TS: parse `import`/`require` statements. For Python: parse `import`/`from`. Other langs: skip gracefully. |
| `quality` | Line length stats (max, avg, >120 chars count), TODO/FIXME/HACK comment count, blank-line ratio |
| `all` | All of the above combined |

**Key constraint:** Files are read from a sandboxed `ANALYSIS_BASE_DIR`. Path traversal (`../`) is blocked.

### 1.2 web-search (search_web)

**What it does:** Calls a search API and returns the top N results (title, URL, snippet).

**v1 approach:** Use SearXNG (self-hosted, no API key needed) as primary backend, with Brave Search API as a fallback option. Both return JSON.

**Why SearXNG:** It can be deployed as a K8s sidecar or separate Deployment — no vendor lock-in, no API key management. Brave Search is the fallback for teams that prefer a hosted API.

### 1.3 db-query (query_database)

**What it does:** Executes a read-only SQL SELECT against a PostgreSQL database and returns results as JSON rows.

**v1 approach:** Connect via `pg` (node-postgres) to a configured database. Enforce read-only at three layers:
1. Application-level: reject non-SELECT queries (existing check, improved)
2. Connection-level: `SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY`
3. Database-level: connect with a Postgres role that only has SELECT grants

---

## 2. Dependencies (npm)

```
npm install pg@^8.13.0           # PostgreSQL client for db-query
```

No new npm dependencies needed for `file-analysis` (uses Node.js `fs` and `path` builtins). No new npm dependency for `web-search` (uses native `fetch()`, available in Node 18+).

Updated `package.json` dependencies section:
```json
{
  "dependencies": {
    "@modelcontextprotocol/sdk": "^1.0.0",
    "express": "^4.21.0",
    "pg": "^8.13.0",
    "redis": "^4.7.0",
    "uuid": "^10.0.0"
  }
}
```

---

## 3. Security Considerations

### 3.1 file-analysis
| Threat | Mitigation |
|--------|------------|
| Path traversal (`../../etc/passwd`) | Resolve path, verify it starts with `ANALYSIS_BASE_DIR`. Reject symlinks pointing outside. |
| Large files (DoS) | Cap file read at 1 MB (`ANALYSIS_MAX_FILE_SIZE`). Return error for larger files. |
| Binary files | Detect via null-byte sampling in first 8KB. Return metadata only (size, type) for binary. |

### 3.2 web-search
| Threat | Mitigation |
|--------|------------|
| SSRF via SearXNG | SearXNG runs with its own egress rules. Gateway only sends query strings, never URLs to fetch. |
| Prompt injection via search results | Results are returned as plain text. Agent must treat them as untrusted. Document this in tool description. |
| API key exposure | Brave API key stored in K8s Secret, mounted as env var. Never logged. |

### 3.3 db-query
| Threat | Mitigation |
|--------|------------|
| SQL injection / mutation | Three-layer read-only enforcement (see section 1.3). |
| SQL with subqueries that mutate | `READ ONLY` transaction mode at connection level blocks writes even in subqueries. |
| Resource exhaustion (huge queries) | `statement_timeout` set to 10 seconds via connection config. `LIMIT` enforced: if query has no LIMIT, append `LIMIT 1000`. |
| Connection string exposure | Stored in K8s Secret. Logged only as `postgres://***@host/db`. |
| Multiple databases | `database` param maps to a predefined allow-list (`DB_REGISTRY` env var as JSON). Arbitrary connection strings are never accepted. |

---

## 4. Configuration (Environment Variables)

```bash
# --- file-analysis ---
ANALYSIS_BASE_DIR=/workspace          # Root directory for file analysis (default: /workspace)
ANALYSIS_MAX_FILE_SIZE=1048576        # Max file size in bytes (default: 1MB)

# --- web-search ---
SEARCH_BACKEND=searxng                # "searxng" or "brave"
SEARXNG_URL=http://searxng:8080       # SearXNG instance URL
BRAVE_API_KEY=                        # Only needed if SEARCH_BACKEND=brave
SEARCH_MAX_RESULTS=10                 # Hard cap on results returned

# --- db-query ---
DB_REGISTRY='{"default":"postgresql://readonly:pass@pghost:5432/appdb"}'
                                      # JSON map of database_id -> connection string
DB_STATEMENT_TIMEOUT=10000            # Statement timeout in ms (default: 10s)
DB_MAX_ROWS=1000                      # Max rows returned (default: 1000)

# --- existing ---
GATEWAY_PORT=3001
REDIS_URL=redis://localhost:6379
```

---

## 5. Implementation Approach

### 5.1 File Structure

```
src/mcp/gateway/
  server.js              # Existing — import handlers from tools/
  tools/
    file-analysis.js     # analyze_file handler
    web-search.js        # search_web handler
    db-query.js          # query_database handler
    index.js             # Re-exports all handlers
```

Each tool module exports a single async function matching the handler signature: `(args) => Promise<{type, text}>`.

### 5.2 file-analysis Implementation

```
1. Validate & resolve path against ANALYSIS_BASE_DIR
2. fs.stat() — collect size, mtime, check against max size
3. fs.readFile() with utf-8
4. Based on analysis_type:
   - structure: count lines, detect language by extension, regex for
     function/class declarations
   - dependencies: language-specific regex for import statements
   - quality: line-length stats, TODO count, blank-line ratio
5. Return JSON-stringified result object
```

Key regex patterns (v1, intentionally simple):
- JS/TS functions: `/^(?:export\s+)?(?:async\s+)?function\s+(\w+)/gm`
- JS/TS classes: `/^(?:export\s+)?class\s+(\w+)/gm`
- Python functions: `/^def\s+(\w+)/gm`
- Python classes: `/^class\s+(\w+)/gm`

### 5.3 web-search Implementation

```
1. Validate query string (non-empty, max 500 chars)
2. If SEARCH_BACKEND === "searxng":
     fetch(`${SEARXNG_URL}/search?q=${encodeURIComponent(query)}&format=json&categories=general`)
   Else if SEARCH_BACKEND === "brave":
     fetch("https://api.search.brave.com/res/v1/web/search", {
       headers: { "X-Subscription-Token": BRAVE_API_KEY }
     })
3. Normalize response to common shape:
     [{ title, url, snippet }]
4. Truncate to max_results (capped by SEARCH_MAX_RESULTS)
5. Return JSON-stringified array
```

### 5.4 db-query Implementation

```
1. Parse database identifier from args (default: "default")
2. Look up connection string in DB_REGISTRY allow-list
3. Create pg.Pool (cached per database id, lazy-initialized)
   - Pool config: max=5, idleTimeoutMillis=30000,
     statement_timeout=DB_STATEMENT_TIMEOUT
4. Acquire client, SET TRANSACTION READ ONLY
5. Validate query:
   a. Trim and uppercase-check starts with SELECT (existing)
   b. Reject if contains semicolons (prevent multi-statement)
   c. If no LIMIT clause, append LIMIT ${DB_MAX_ROWS}
6. Execute query
7. Return { columns: [...], rows: [...], rowCount } as JSON
8. Release client back to pool
```

Connection pool lifecycle: pools are created on first use and stored in a module-level Map. On SIGTERM/SIGINT, all pools are drained before exit.

---

## 6. Test Strategy

### 6.1 Unit Tests (per tool module)

**file-analysis:**
- Path traversal rejection (various `../` patterns, symlinks)
- Correct line count, function extraction for sample JS/Python files
- Binary file detection
- File size limit enforcement
- Each analysis_type returns expected keys

**web-search:**
- Query validation (empty, too long)
- Response normalization from SearXNG JSON shape
- Response normalization from Brave JSON shape
- max_results capping

**db-query:**
- SELECT-only enforcement (reject INSERT, UPDATE, DELETE, DROP, TRUNCATE)
- Semicolon rejection
- LIMIT injection when missing
- Database ID allow-list enforcement
- Statement timeout behavior (mock pg to simulate slow query)

### 6.2 Integration Tests

- **file-analysis:** Mount a temp directory with known files, invoke handler, verify output.
- **web-search:** Spin up a mock HTTP server returning canned SearXNG responses. Verify end-to-end.
- **db-query:** Use `pg-mem` (in-memory Postgres) or a test container. Create a table, insert rows, verify SELECT returns data and INSERT is blocked.

### 6.3 Gateway-Level Tests

- Start the gateway with all three real handlers.
- Connect an MCP client via SSE.
- Call each tool and verify non-placeholder responses.
- Verify tool scoping still works (Redis `gateway_scope` restricts available tools).

### 6.4 Test Framework

Use Node.js built-in test runner (`node:test`) + `node:assert`. No additional test framework dependency. Add to `package.json`:

```json
"scripts": {
  "start": "node server.js",
  "test": "node --test tools/**/*.test.js",
  "test:integration": "node --test tests/**/*.test.js"
}
```

---

## 7. Effort Estimate

| Task | Effort | Notes |
|------|--------|-------|
| file-analysis handler | 3-4 hours | Straightforward fs + regex |
| web-search handler | 2-3 hours | Mostly API integration + normalization |
| db-query handler | 4-5 hours | Pool management, security layers, LIMIT injection |
| Tool module structure + wiring | 1 hour | Extract from server.js, re-wire imports |
| Unit tests (all 3 tools) | 4-5 hours | ~15-20 test cases total |
| Integration tests | 3-4 hours | Mock servers, pg-mem setup |
| K8s config updates (env vars, secrets) | 1-2 hours | Helm values / kustomize overlays |
| Documentation | 1 hour | Tool descriptions, env var reference |
| **Total** | **~20-24 hours** | ~3 days of focused work |

---

## 8. Architectural Decision

### ADR: Tool Handler Extraction

**Context:** All three tool handlers are currently inline in `server.js`. As they grow from placeholders to real implementations with dependencies (pg, fs, fetch), keeping them inline will make `server.js` hard to maintain and test.

**Decision:** Extract each handler into its own module under `tools/`. The `TOOL_REGISTRY` in `server.js` imports handlers from `tools/index.js`. Each tool module is independently testable.

**Consequences:**
- (+) Each tool can be tested in isolation
- (+) Dependencies are scoped (pg only imported by db-query)
- (+) New tools follow a clear pattern: add module, register in index
- (-) Slightly more files to navigate (mitigated by clear naming)

---

## 9. Implementation Order

1. **Tool module structure** — Extract existing placeholders into `tools/` directory
2. **file-analysis** — No external dependencies, fastest to validate the pattern
3. **db-query** — Highest security sensitivity, implement early to allow security review
4. **web-search** — Depends on SearXNG deployment decision (can stub with Brave initially)
5. **Tests** — Written alongside each tool, integration tests last
6. **K8s config** — Update Helm/kustomize with new env vars and secrets
