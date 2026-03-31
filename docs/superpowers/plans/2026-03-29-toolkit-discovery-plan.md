# Ditto Factory Toolkit Discovery & Registration — Implementation Plan

**Spec:** `docs/superpowers/specs/2026-03-29-toolkit-discovery-design.md`

## Task Order

Tasks are ordered by dependency. Backend first, then frontend.

---

### Task 1: Toolkit Data Models + DB Tables

**What:** Create the data models and ensure SQLite tables exist on startup.

**Files to create:**
- `controller/src/controller/toolkits/models.py` — Dataclasses: `ToolkitSource`, `Toolkit`, `ToolkitVersion`, `DiscoveredItem` (manifest entry), `DiscoveryManifest`
  - Enums: `ToolkitType` (skill, plugin, profile, tool), `LoadStrategy` (mount_file, install_plugin, inject_rules, install_package), `RiskLevel` (safe, moderate, high), `ToolkitStatus` (available, disabled, update_available, error)

**Files to modify:**
- `controller/src/controller/main.py` — Add `CREATE TABLE IF NOT EXISTS` for `toolkit_sources`, `toolkits`, `toolkit_versions` in the lifespan startup (same pattern as skills/workflows tables)

**Acceptance:** Tables are created on controller startup. Models importable. No API yet.

---

### Task 2: GitHub Client

**What:** Create a GitHub API client for fetching repo metadata, file trees, and file contents.

**Files to create:**
- `controller/src/controller/toolkits/github_client.py`
  - `GitHubClient` class with `httpx.AsyncClient`
  - `get_repo_info(owner, repo)` → repo metadata (description, default_branch, stars)
  - `get_tree(owner, repo, branch)` → recursive file tree
  - `get_file_content(owner, repo, path, ref)` → raw file content (base64 decoded)
  - `get_latest_commit(owner, repo, branch)` → commit SHA + date
  - `parse_github_url(url)` → extracts owner, repo, branch, path from any GitHub URL format
  - Unauthenticated by default, optional `DF_GITHUB_TOKEN` for rate limits / private repos

**Acceptance:** Can fetch file tree and content from any public GitHub repo. URL parser handles full repo, subdirectory, and file URLs.

---

### Task 3: Discovery Engine

**What:** Analyze a GitHub repo and produce a structured manifest of discovered toolkits.

**Files to create:**
- `controller/src/controller/toolkits/discovery.py`
  - `DiscoveryEngine` class
  - `discover(github_url, branch?)` → `DiscoveryManifest`
  - Detection heuristics (scan file tree for patterns):
    - `.claude/skills/**/*.md` or `skills/**/*.md` → skill
    - `.claude-plugin/` directory → plugin
    - `profiles/` or `.claude/rules/` → profile
    - Category directories containing `.md` files (like agency-agents: `engineering/`, `design/`, `marketing/`) → skills
    - `mcp.json` or MCP server source files → tool
    - `package.json` with MCP-related deps → tool
    - `pyproject.toml` with CLI entry points → tool
  - For each discovered item:
    - Extract name from filename or YAML frontmatter or first heading
    - Extract description from frontmatter or first paragraph
    - Auto-generate tags from directory path and content keywords
    - Classify risk level based on type and content
    - Determine load strategy from type
  - Parse YAML frontmatter from `.md` files (handle `---` delimited blocks)
  - Generate recommendations (which items to import, conflicts with existing)

**Acceptance:** Given `https://github.com/obra/superpowers`, produces manifest with ~15 skills detected. Given `https://github.com/msitarzewski/agency-agents`, detects skills organized by category. Handles repos with `.claude-plugin/` directories.

---

### Task 4: Toolkit Registry (CRUD)

**What:** Database operations for toolkit sources, toolkits, and versions.

**Files to create:**
- `controller/src/controller/toolkits/registry.py`
  - `ToolkitRegistry` class (takes `db_path`)
  - Source CRUD: `create_source`, `get_source`, `list_sources`, `delete_source`, `update_source_sync`
  - Toolkit CRUD: `create_toolkit`, `get_toolkit`, `list_toolkits`, `update_toolkit`, `delete_toolkit` (soft delete)
  - `import_from_manifest(source_id, items: list[DiscoveredItem])` — bulk create toolkits + initial versions
  - Version operations: `create_version`, `get_versions`, `rollback`
  - `check_for_updates(source_id)` — compare pinned SHA to latest, return list of toolkits with changes
  - `apply_update(toolkit_slug, new_content, new_sha)` — create new version, update toolkit

**Acceptance:** Full CRUD works against SQLite. Import from manifest creates toolkits with versions. Update detection works when SHA differs.

---

### Task 5: Toolkit API Endpoints

**What:** REST API for all toolkit operations.

**Files to create:**
- `controller/src/controller/toolkits/api.py`
  - Router with prefix `/api/v1/toolkits`
  - Endpoints:
    - `POST /sources` — add a GitHub source
    - `GET /sources` — list sources
    - `GET /sources/{id}` — get source
    - `DELETE /sources/{id}` — remove source
    - `POST /sources/{id}/sync` — check for updates from source
    - `POST /discover` — run discovery on a URL, return manifest
    - `POST /import` — import selected items from manifest
    - `GET /` — list all toolkits (filterable by type, status, source)
    - `GET /{slug}` — get toolkit details + content
    - `PUT /{slug}` — update toolkit metadata
    - `DELETE /{slug}` — disable toolkit
    - `GET /{slug}/versions` — version history
    - `POST /{slug}/rollback` — rollback to version
    - `POST /{slug}/update` — apply pending update
  - Pydantic request/response models
  - Dependency injection for registry and discovery engine

**Files to modify:**
- `controller/src/controller/main.py` — Initialize `ToolkitRegistry` and `DiscoveryEngine`, mount router, wire up dependency overrides

**Acceptance:** All endpoints return correct responses. Discovery endpoint analyzes a real GitHub URL and returns manifest. Import endpoint creates toolkits in DB.

---

### Task 6: Frontend — Toolkit Types + Hooks + API

**What:** TypeScript types, API hooks, and shared components for the toolkit feature.

**Files to create:**
- Add toolkit types to `web/src/lib/types.ts`:
  - `ToolkitSource`, `Toolkit`, `ToolkitVersion`, `DiscoveredItem`, `DiscoveryManifest`
  - Enums: `ToolkitType`, `LoadStrategy`, `RiskLevel`, `ToolkitStatus`
- Add hooks to `web/src/lib/hooks.ts`:
  - `useToolkitSources`, `useToolkits`, `useToolkit`, `useToolkitVersions`
  - `useCreateSource`, `useDeleteSource`, `useSyncSource`
  - `useDiscover` (mutation — takes URL, returns manifest)
  - `useImportToolkits` (mutation — takes source_id + selected items)
  - `useDeleteToolkit`, `useRollbackToolkit`, `useUpdateToolkit`

**Acceptance:** Types compile. Hooks match API endpoints.

---

### Task 7: Frontend — Toolkits List Page

**What:** Main toolkits page showing all registered toolkits.

**Files to create:**
- `web/src/app/toolkits/page.tsx` — list page with filters
- `web/src/components/toolkits/toolkit-table.tsx` — table with: name, type badge (color-coded), source repo link, version, status badge, usage count, actions
- Update sidebar to add "Toolkits" nav item (between Skills and Workflows)

**Design:**
- Type badges: skill=purple, plugin=blue, profile=green, tool=orange
- Status badges: available=green, disabled=gray, update_available=yellow, error=red
- Risk indicator: safe=no icon, moderate=yellow warning, high=red shield
- Filter bar: type dropdown, status dropdown, source dropdown, text search

**Acceptance:** Toolkits page renders with empty state. Sidebar has Toolkits link.

---

### Task 8: Frontend — Import Flow (Discovery + Review + Import)

**What:** The guided multi-step import flow.

**Files to create:**
- `web/src/app/toolkits/import/page.tsx` — multi-step import page
- `web/src/components/toolkits/import-url-input.tsx` — Step 1: GitHub URL input with branch selector
- `web/src/components/toolkits/discovery-results.tsx` — Step 2: Interactive manifest review
  - Checklist of discovered items with select/deselect
  - Each item shows: name, type badge, description, risk level, path in repo
  - Expandable content preview (rendered markdown)
  - Override controls: type dropdown, tags input, toggle import
  - Summary bar: "X of Y items selected"
- `web/src/components/toolkits/import-confirm.tsx` — Step 3: Confirmation with progress

**Design:**
- Wizard-style stepper at top (URL → Review → Import)
- Discovery loading state: animated scanner visual or progress dots
- Manifest items: card list (not table) for better content preview UX
- Each card has checkbox, type badge, risk indicator, expandable section

**Acceptance:** Full import flow works end-to-end: enter URL → see discovered items → select → import → see them in toolkit list.

---

### Task 9: Frontend — Toolkit Detail + Versions + Updates

**What:** Toolkit detail page with content viewer, version history, and update flow.

**Files to create:**
- `web/src/app/toolkits/[slug]/page.tsx` — detail page
- `web/src/components/toolkits/toolkit-detail.tsx` — content viewer + metadata panel
- `web/src/components/toolkits/toolkit-versions.tsx` — version history timeline with rollback
- `web/src/components/toolkits/toolkit-update.tsx` — update available banner + diff viewer

**Design:**
- Two-column layout: left 60% content viewer (markdown rendered), right 40% metadata
- Metadata panel: type, source repo (linked), version, pinned SHA (monospace), tags, dependencies, risk, status
- Version history: vertical timeline, each entry shows version number, date, changelog, rollback button
- Update banner: yellow alert at top when `status === update_available`, click to see diff, approve button

**Acceptance:** Detail page shows toolkit content and metadata. Version history with rollback works. Update flow shows diff and applies update.

---

### Task 10: Frontend — Sources Management

**What:** Sources page for managing registered GitHub repos.

**Files to create:**
- `web/src/app/toolkits/sources/page.tsx` — sources list
- `web/src/components/toolkits/source-table.tsx` — table with: repo name (linked), branch, last synced, commit SHA, toolkit count, "Check updates" button, delete button

**Acceptance:** Sources page shows all registered sources. "Check for updates" triggers sync and shows results.
