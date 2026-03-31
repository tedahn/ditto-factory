# Ditto Factory Toolkit Discovery & Registration — Design Spec

## Overview

A guided discovery system that lets users import tools, skills, plugins, and profiles from GitHub repositories into the Ditto Factory toolkit registry. Registered toolkits become available for use in workflows and tasks (composed separately in Flow 2).

Discovery is not one-and-done — toolkits evolve. The system tracks source repos, pinned versions, and surfaces updates so users can review and upgrade.

## Scope

**In scope (Flow 1):**
- GitHub repo analysis and toolkit detection
- Guided import with user configuration
- Toolkit registry (DB model, API, UI)
- Versioning and update detection
- Sync/refresh from source repos

**Out of scope (Flow 2 — separate feature):**
- Workflow composition using toolkits
- Task type → toolkit mapping
- Classifier integration
- Agent pod loadout assembly

## Toolkit Types

| Type | What It Is | Load Strategy | Examples |
|------|-----------|---------------|----------|
| **skill** | Markdown file with instructions/methodology | Mount to `.claude/skills/` in agent pod | superpowers skills, agency-agents personas |
| **plugin** | Claude Code plugin (`.claude-plugin/` dir) | Install plugin, configure in agent env | beads, context-mode, ui-ux-pro-max |
| **profile** | Codebase standards, rules, conventions | Inject into CLAUDE.md or `.claude/rules/` | agent-os profiles, best-practice rules |
| **tool** | CLI/MCP server package | Install package, add to `mcp.json` | agent-reach tools |

## Data Model

### Toolkit Source

Represents a GitHub repository that has been analyzed.

```
toolkit_sources:
  id              TEXT PRIMARY KEY
  github_url      TEXT NOT NULL        -- e.g. "github.com/obra/superpowers"
  github_owner    TEXT NOT NULL
  github_repo     TEXT NOT NULL
  branch          TEXT DEFAULT 'main'
  last_commit_sha TEXT                 -- pinned commit at last sync
  last_synced_at  TIMESTAMP
  status          TEXT DEFAULT 'active'  -- active | disabled | error
  metadata        TEXT DEFAULT '{}'    -- repo description, stars, etc.
  created_at      TIMESTAMP
  updated_at      TIMESTAMP
```

### Toolkit

An individual importable item discovered within a source.

```
toolkits:
  id              TEXT PRIMARY KEY
  source_id       TEXT REFERENCES toolkit_sources(id)
  slug            TEXT UNIQUE NOT NULL
  name            TEXT NOT NULL
  type            TEXT NOT NULL        -- skill | plugin | profile | tool
  description     TEXT DEFAULT ''
  path            TEXT NOT NULL        -- path within repo (e.g. "skills/tdd/SKILL.md")
  load_strategy   TEXT NOT NULL        -- mount_file | install_plugin | inject_rules | install_package
  version         INTEGER DEFAULT 1
  pinned_sha      TEXT                 -- commit SHA this version was imported from
  content         TEXT DEFAULT ''      -- cached content (for skills/profiles)
  config          TEXT DEFAULT '{}'    -- type-specific config (mcp.json fragment, deps, etc.)
  tags            TEXT DEFAULT '[]'
  dependencies    TEXT DEFAULT '[]'    -- npm packages, pip packages, CLI tools needed
  risk_level      TEXT DEFAULT 'safe'  -- safe | moderate | high
  status          TEXT DEFAULT 'available'  -- available | disabled | update_available | error
  usage_count     INTEGER DEFAULT 0
  is_active       INTEGER DEFAULT 1
  created_at      TIMESTAMP
  updated_at      TIMESTAMP
```

### Toolkit Version

Tracks version history for updates.

```
toolkit_versions:
  id              TEXT PRIMARY KEY
  toolkit_id      TEXT REFERENCES toolkits(id)
  version         INTEGER NOT NULL
  pinned_sha      TEXT NOT NULL
  content         TEXT DEFAULT ''
  config          TEXT DEFAULT '{}'
  changelog       TEXT                 -- what changed (auto-generated from git diff)
  created_at      TIMESTAMP
```

## Discovery Flow (Guided)

### Step 1: User provides GitHub URL

User enters a URL in the dashboard. Can be:
- Full repo: `https://github.com/obra/superpowers`
- Subdirectory: `https://github.com/obra/superpowers/tree/main/skills/tdd`
- Specific file: `https://github.com/obra/superpowers/blob/main/skills/tdd/SKILL.md`

### Step 2: Discovery agent analyzes the repo

A discovery agent (new agent type) clones/fetches the repo and produces a manifest:

**Detection heuristics:**
1. Scan for known directory patterns:
   - `.claude/skills/` or `skills/` → skill type
   - `.claude-plugin/` → plugin type
   - `profiles/` or `.claude/rules/` → profile type
   - `mcp.json` or MCP server source → tool type
2. For each discovered item, read the file to extract:
   - Name and description (from frontmatter, headings, or filename)
   - Tags and capabilities
   - Dependencies
3. Classify risk level:
   - `safe`: pure markdown, no code execution
   - `moderate`: has scripts, hooks, or config that modifies agent behavior
   - `high`: installs packages, runs binaries, has network access

**Output: Discovery manifest** (structured JSON shown to user in dashboard)

### Step 3: User reviews and configures

Dashboard shows the manifest as an interactive checklist:
- Each discovered toolkit shown with: name, type, description, risk level, recommendation
- User can: select/deselect items, override type classification, edit tags, adjust config
- User can preview the content of each item before importing
- For plugins/tools: show required dependencies and confirm installation

### Step 4: Import

Selected toolkits are:
1. Stored in the `toolkits` table with content cached
2. Initial version created in `toolkit_versions`
3. Source pinned to current commit SHA
4. Status set to `available`

## Update Flow

### Detecting updates

Two mechanisms:

**A) Manual check:** User clicks "Check for updates" on a source or toolkit in the dashboard. System fetches latest commit from GitHub, compares SHA to pinned version.

**B) Periodic sync (optional):** Background task checks pinned sources on a configurable interval (e.g., daily). Marks toolkits as `update_available` when source has new commits.

### Applying updates

When an update is available:
1. Dashboard shows diff — what changed in the source file(s) since the pinned SHA
2. User reviews the changes (similar to a PR review)
3. User approves → new version created, content updated, SHA re-pinned
4. User can also rollback to any previous version

### Version management

- Each update creates a new entry in `toolkit_versions`
- Current version is always in `toolkits.version` + `toolkits.pinned_sha`
- Rollback: restore content/config from a previous version entry
- Breaking changes: if the toolkit type or load strategy changes between versions, flag for user attention

## API Endpoints

```
# Toolkit Sources
POST   /api/v1/toolkits/sources              — Add a GitHub source URL
GET    /api/v1/toolkits/sources              — List sources
GET    /api/v1/toolkits/sources/{id}         — Get source details
DELETE /api/v1/toolkits/sources/{id}         — Remove source
POST   /api/v1/toolkits/sources/{id}/sync    — Trigger re-sync / check for updates

# Discovery
POST   /api/v1/toolkits/discover             — Start discovery for a URL
  Body: { github_url, branch? }
  Response: { source_id, manifest: [...discovered items...] }

# Toolkits
POST   /api/v1/toolkits/import               — Import selected toolkits from manifest
  Body: { source_id, items: [{ path, name?, type?, tags?, config? }] }
GET    /api/v1/toolkits                       — List all registered toolkits
GET    /api/v1/toolkits/{slug}               — Get toolkit details + content
PUT    /api/v1/toolkits/{slug}               — Update toolkit metadata
DELETE /api/v1/toolkits/{slug}               — Disable/remove toolkit
GET    /api/v1/toolkits/{slug}/versions      — Version history
POST   /api/v1/toolkits/{slug}/rollback      — Rollback to version
POST   /api/v1/toolkits/{slug}/update        — Apply pending update from source
```

## Dashboard Pages

### Toolkits page (`/toolkits`)
- **List view:** All registered toolkits — name, type badge, source repo, version, status, usage count
- **Filters:** By type (skill/plugin/profile/tool), status, source repo
- **Import button:** Opens the discovery flow

### Import flow (`/toolkits/import`)
- **Step 1:** Input field for GitHub URL + branch selector
- **Step 2:** Loading state while discovery agent works
- **Step 3:** Interactive manifest review — checklist of discovered items with type badges, risk indicators, content preview expandable
- **Step 4:** Confirmation + import progress

### Toolkit detail (`/toolkits/[slug]`)
- Content viewer (markdown rendered)
- Metadata: type, source, version, SHA, tags, dependencies, risk
- Version history with rollback
- Update available banner (when source has new commits)
- Diff viewer for pending updates

### Sources page (`/toolkits/sources`)
- List of all registered source repos
- Last synced, commit SHA, toolkit count per source
- "Check for updates" button per source
- Status indicators

## Backend Implementation

### Discovery Agent

New agent type `discovery` that:
1. Receives a GitHub URL
2. Uses the GitHub API (or `git clone --depth 1`) to fetch repo contents
3. Runs detection heuristics (pattern matching on directory structure + file content)
4. Produces structured manifest JSON
5. Returns manifest to the controller

For the initial implementation, this can be a **controller-side service** (Python, no K8s pod needed) since it's just fetching files and analyzing them — doesn't need Claude Code.

### GitHub Integration

Use the GitHub API (unauthenticated for public repos, token for private):
- `GET /repos/{owner}/{repo}/git/trees/{sha}?recursive=true` — full file tree
- `GET /repos/{owner}/{repo}/contents/{path}` — file content
- `GET /repos/{owner}/{repo}/commits/{branch}` — latest commit SHA

### File structure

```
controller/src/controller/toolkits/
├── models.py          — ToolkitSource, Toolkit, ToolkitVersion dataclasses
├── discovery.py       — Repo analyzer, pattern detection, manifest generation
├── registry.py        — CRUD operations for toolkits + sources
├── github_client.py   — GitHub API wrapper (fetch tree, contents, commits)
├── api.py             — REST API endpoints
└── updater.py         — Version comparison, diff generation, update logic
```

## Out of Scope

- Private repo authentication (can add later with GitHub token)
- Automatic toolkit-to-task-type mapping (Flow 2)
- Agent pod loadout composition (Flow 2)
- Marketplace / curated index (future)
- Webhook-based auto-sync (future — would use GitHub webhooks to push updates)
