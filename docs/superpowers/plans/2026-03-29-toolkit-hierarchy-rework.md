# Toolkit Hierarchy Rework — Implementation Plan

**Problem:** Toolkits are imported as flat individual files, losing the hierarchical structure and interdependencies. `systematic-debugging` becomes 8 separate entries instead of one skill with sub-files.

**Solution:** Toolkit = repo-level unit. Components = individual skills/plugins/profiles within it. Components can have sub-files.

## New Data Model

```
Toolkit (1 per repo)
  ├── source: obra/superpowers @ main @ sha:abc123
  ├── type: skill-collection | plugin | profile-pack | tool
  └── Components (the usable units within)
        ├── systematic-debugging (skill)
        │     ├── SKILL.md (primary file)
        │     ├── condition-based-waiting.md (sub-file)
        │     ├── defense-in-depth.md (sub-file)
        │     └── root-cause-tracing.md (sub-file)
        ├── brainstorming (skill)
        │     ├── SKILL.md (primary file)
        │     ├── visual-companion.md (sub-file)
        │     └── spec-document-reviewer-prompt.md (sub-file)
        └── test-driven-development (skill)
              ├── SKILL.md (primary file)
              └── testing-anti-patterns.md (sub-file)
```

## DB Schema Changes

### Rename `toolkits` → keep as toolkit (repo-level)

Drop per-file rows. One row per imported repo:

```sql
-- Existing table repurposed (drop and recreate)
DROP TABLE IF EXISTS toolkit_versions;
DROP TABLE IF EXISTS toolkits;

CREATE TABLE toolkits (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES toolkit_sources(id),
    slug TEXT UNIQUE NOT NULL,          -- e.g. "superpowers"
    name TEXT NOT NULL,                 -- e.g. "Superpowers"
    type TEXT NOT NULL,                 -- skill-collection | plugin | profile-pack | tool
    description TEXT DEFAULT '',
    version INTEGER DEFAULT 1,
    pinned_sha TEXT,
    status TEXT DEFAULT 'available',
    tags TEXT DEFAULT '[]',
    component_count INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT (datetime('now')),
    updated_at TIMESTAMP DEFAULT (datetime('now'))
);

CREATE TABLE toolkit_versions (
    id TEXT PRIMARY KEY,
    toolkit_id TEXT NOT NULL REFERENCES toolkits(id),
    version INTEGER NOT NULL,
    pinned_sha TEXT NOT NULL,
    changelog TEXT,
    created_at TIMESTAMP DEFAULT (datetime('now'))
);
```

### New `toolkit_components` table

```sql
CREATE TABLE toolkit_components (
    id TEXT PRIMARY KEY,
    toolkit_id TEXT NOT NULL REFERENCES toolkits(id),
    slug TEXT NOT NULL,                 -- e.g. "systematic-debugging"
    name TEXT NOT NULL,                 -- e.g. "Systematic Debugging"
    type TEXT NOT NULL,                 -- skill | plugin | profile | tool | agent
    description TEXT DEFAULT '',
    directory TEXT NOT NULL,            -- e.g. "skills/systematic-debugging"
    primary_file TEXT NOT NULL,         -- e.g. "skills/systematic-debugging/SKILL.md"
    load_strategy TEXT DEFAULT 'mount_file',
    content TEXT DEFAULT '',            -- primary file content cached
    tags TEXT DEFAULT '[]',
    risk_level TEXT DEFAULT 'safe',
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT (datetime('now')),
    UNIQUE(toolkit_id, slug)
);

CREATE TABLE toolkit_component_files (
    id TEXT PRIMARY KEY,
    component_id TEXT NOT NULL REFERENCES toolkit_components(id),
    path TEXT NOT NULL,                 -- relative path within repo
    filename TEXT NOT NULL,             -- just the filename
    content TEXT DEFAULT '',            -- cached content
    is_primary INTEGER DEFAULT 0,      -- 1 for the main SKILL.md / entry point
    created_at TIMESTAMP DEFAULT (datetime('now')),
    UNIQUE(component_id, path)
);
```

---

## Task Breakdown

### Task 1: Update Models

**What:** Replace flat toolkit model with hierarchical Toolkit → Component → File structure.

**Files to modify:**
- `controller/src/controller/toolkits/models.py`:
  - Update `Toolkit` dataclass: remove file-level fields (content, path, load_strategy, config, dependencies, risk_level). Add `component_count`, change `type` enum to include `skill_collection`.
  - Add `ToolkitComponent` dataclass: slug, name, type, description, directory, primary_file, load_strategy, content, tags, risk_level, is_active
  - Add `ComponentFile` dataclass: path, filename, content, is_primary
  - Update `DiscoveredItem` → `DiscoveredComponent`: represents a component (directory-level), contains list of files
  - Update `DiscoveryManifest`: `discovered` becomes list of `DiscoveredComponent`
  - Add `ToolkitTypeCategory` enum: `skill_collection`, `plugin`, `profile_pack`, `tool`

**Acceptance:** Models compile, represent the hierarchy correctly.

---

### Task 2: Update DB Table Init + Migration

**What:** Update the table creation in `main.py` to create the new schema. Since this is local dev with SQLite, drop old tables and recreate.

**Files to modify:**
- `controller/src/controller/main.py`: Replace the toolkit table creation block with the new schema (toolkits, toolkit_versions, toolkit_components, toolkit_component_files). Add migration logic: if old `toolkits` table has a `path` column, drop all toolkit tables and recreate.

**Acceptance:** Controller starts clean with new tables.

---

### Task 3: Rework Discovery Engine

**What:** Change discovery to detect components as directory-level units, not individual files.

**Files to modify:**
- `controller/src/controller/toolkits/discovery.py`:
  - Classify the repo itself (is it a skill-collection, plugin, profile-pack, or tool?)
  - Group files by parent directory into components:
    - `skills/systematic-debugging/` → one component with all files inside
    - `skills/brainstorming/` → one component
    - For flat repos (agency-agents), each `.md` in a category dir is its own component (single-file component)
  - Each component identifies its primary file (SKILL.md, plugin.json, or the main .md file)
  - Sub-files are captured as `ComponentFile` entries
  - Filter out non-component files: README.md, LICENSE, .github/*, docs/*, tests/*, etc.
  - Return `DiscoveryManifest` with `DiscoveredComponent` list

**Key heuristics for grouping:**
- If a directory has a `SKILL.md`, it's a skill component — all other .md files in that dir are sub-files
- If a directory has a single .md file, that file is both primary and only file
- `.claude-plugin/` directory = one plugin component
- `agents/*.md` = each file is a separate agent component (single-file)
- Category dirs (engineering/, design/) = each .md is a component tagged with the category

**Acceptance:** `discover("https://github.com/obra/superpowers")` returns ~15 components (not 44), each with their sub-files properly grouped.

---

### Task 4: Rework Registry CRUD

**What:** Update all registry operations for the hierarchical model.

**Files to modify:**
- `controller/src/controller/toolkits/registry.py`:
  - `create_toolkit` → creates a toolkit row (repo-level)
  - `import_from_manifest` → creates toolkit + all components + all component files in one transaction
  - Add `list_components(toolkit_id)`, `get_component(toolkit_id, slug)`, `get_component_files(component_id)`
  - Update `list_toolkits` to include `component_count`
  - Version operations work at toolkit level (not component level)
  - Update operations: `apply_update` refreshes all component files from source

**Acceptance:** CRUD works for toolkit → component → file hierarchy.

---

### Task 5: Rework API Endpoints

**What:** Update all API endpoints and response models for the hierarchy.

**Files to modify:**
- `controller/src/controller/toolkits/api.py`:
  - `ToolkitResponse`: remove file-level fields, add `component_count`, `components` (optional, included on detail)
  - Add `ComponentResponse`: slug, name, type, description, directory, primary_file, tags, risk_level, file_count
  - Add `ComponentFileResponse`: path, filename, is_primary
  - Add `ComponentDetailResponse`: includes `files` list and `content` (primary file content)
  - Update list endpoint: returns toolkits with component_count
  - Update detail endpoint: returns toolkit with full component list
  - Add `GET /{slug}/components` — list components
  - Add `GET /{slug}/components/{component_slug}` — component detail with files and content
  - Update import endpoint to work with new manifest format
  - Discovery endpoint returns grouped components

**Acceptance:** API returns hierarchical data. Detail view includes components.

---

### Task 6: Rework Frontend Types + Hooks

**What:** Update TypeScript types and hooks for the hierarchy.

**Files to modify:**
- `web/src/lib/types.ts`:
  - Update `Toolkit` interface: remove file-level fields, add `component_count`
  - Add `ToolkitComponent` interface
  - Add `ComponentFile` interface
  - Update `DiscoveredItem` → `DiscoveredComponent` with `files` list
- `web/src/lib/hooks.ts`:
  - Add `useToolkitComponents(slug)` — GET /{slug}/components
  - Add `useToolkitComponent(slug, componentSlug)` — GET /{slug}/components/{componentSlug}
  - Update existing hooks for new response shapes

**Acceptance:** Types compile, hooks match new API.

---

### Task 7: Rework Frontend Pages

**What:** Update all toolkit UI pages for the hierarchical model.

**Files to modify:**
- `web/src/components/toolkits/toolkit-table.tsx`: Show toolkit name, type, source, component count, version, status. Remove per-file columns.
- `web/src/app/toolkits/[slug]/page.tsx`: Toolkit detail now shows a list of components (cards), not raw file content. Each component card shows name, type badge, description, file count, "View" link.
- Add `web/src/app/toolkits/[slug]/components/[componentSlug]/page.tsx`: Component detail — shows primary file content + list of sub-files with expandable content.
- `web/src/components/toolkits/discovery-results.tsx`: Show discovered components (grouped), not flat file list. Each component shows its files as a nested list.
- `web/src/components/toolkits/toolkit-detail.tsx`: Rework to show toolkit metadata + component grid.

**Acceptance:** Toolkits page shows ~7 repos (not 44 files). Detail page shows components within a toolkit. Import flow shows grouped components.

---

### Task 8: Re-seed with Correct Structure

**What:** Clear old data and re-run seeder with the new hierarchical discovery.

**Steps:**
- The migration in Task 2 will drop old tables on startup
- Seeder runs automatically on first start (no sources exist)
- Verify: superpowers imports as 1 toolkit with ~15 components

**Acceptance:** After restart, `GET /api/v1/toolkits/` returns 1 toolkit (superpowers) with `component_count: ~15`. Components are properly grouped.
