# Phase 2: Agent-Driven Toolkit Onboarding — Implementation Plan

**Goal:** Replace pattern-matching discovery with LLM reasoning for correct classification, component grouping, and relationship mapping.

**Approach:** Call Claude API directly from the controller (no K8s pods needed). Clone repo → gather context → send to Claude → parse structured response → import.

---

## Task 1: Repo Analyzer Service

**What:** A new service that clones a repo and gathers the context an LLM needs to analyze it.

**File to create:** `controller/src/controller/toolkits/analyzer.py`

**Class: `RepoAnalyzer`**

```python
class RepoAnalyzer:
    """Clones a repo and gathers context for LLM analysis."""
    
    async def gather_context(self, github_url: str, branch: str = "main") -> dict:
        """Clone repo, read key files, return structured context.
        
        Returns:
        {
            "url": "...",
            "owner": "...",
            "repo": "...",
            "branch": "...",
            "readme": "...",           # README.md content
            "claude_md": "...",        # CLAUDE.md if present
            "package_json": "...",     # package.json if present
            "pyproject_toml": "...",   # pyproject.toml if present
            "directory_tree": "...",   # `find . -type f` output (filtered)
            "key_files": {             # Content of important files
                "path": "content",
                ...
            },
            "file_count": 123,
            "latest_tag": "v5.0.6",   # from git tags
        }
        """
```

**Implementation:**
1. `git clone --depth 1 --branch {branch} {url} {tmpdir}` (shallow clone)
2. Read README.md, CLAUDE.md, .claude-plugin/, package.json, pyproject.toml
3. Generate directory tree (filtered: exclude .git, node_modules, __pycache__, etc.)
4. Read first ~500 chars of each .md file in skills/, agents/, commands/ directories
5. Get latest tag from `git describe --tags --abbrev=0`
6. Clean up tmpdir

**Budget:** Keep total gathered context under 50KB to fit in a single Claude API call.

---

## Task 2: LLM Onboarding Prompt + Parser

**What:** The prompt that tells Claude how to analyze a repo and the response parser.

**File to create:** `controller/src/controller/toolkits/llm_onboarder.py`

**Class: `LLMOnboarder`**

```python
class LLMOnboarder:
    """Uses Claude API to analyze a repo and produce a structured manifest."""
    
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
    
    async def analyze(self, context: dict) -> DiscoveryManifest:
        """Send repo context to Claude, get back a structured manifest."""
```

**The prompt should instruct Claude to:**
1. **Classify the repo** — What is this? (agent persona library, development methodology framework, capability extension toolkit, persistent memory system, etc.)
2. **Identify components** — What are the real, usable units? Not internal docs, test files, or examples.
3. **Map relationships** — Which components work together? Are there pipelines, dependencies, or groupings?
4. **Extract metadata** — Name, description, tags, risk level, load strategy for each component.
5. **Determine the primary entry point** — What file/component is the "main" one?
6. **Identify what type each component is** — skill (methodology/process), agent (persona), command (slash command), plugin (MCP/tooling), profile (standards/rules)

**The prompt includes examples** of correctly classified repos:
- superpowers: "development methodology framework" with skill pipeline
- agency-agents: "agent persona library" organized by domain
- beads: "persistent memory system" with CLI + plugin + workflows

**Response format:** JSON matching `DiscoveryManifest` schema, requested via Claude's tool_use or JSON mode.

**Parser:** Validate the response, handle partial/malformed responses gracefully, fall back to pattern-matching discovery if LLM call fails.

---

## Task 3: Integrate into Discovery Flow

**What:** Update the discover endpoint to use LLM analysis when an Anthropic API key is available.

**Files to modify:**
- `controller/src/controller/toolkits/api.py` — Update `POST /discover` endpoint:
  1. If Anthropic API key is configured → use LLM onboarding path
  2. If not → fall back to existing pattern-matching discovery
  3. Return the same `DiscoveryResponse` format either way

- `controller/src/controller/main.py` — Initialize `RepoAnalyzer` and `LLMOnboarder`, wire into dependency injection

**New dependency:**
- `controller/src/controller/toolkits/api.py` — Add `get_llm_onboarder()` dependency

**Flow:**
```
POST /discover {github_url}
  → RepoAnalyzer.gather_context(url) → context dict
  → LLMOnboarder.analyze(context) → DiscoveryManifest
  → Return as DiscoveryResponse
```

---

## Task 4: Update Frontend for LLM-Analyzed Manifests

**What:** The discovery results page should show the richer data from LLM analysis.

**Files to modify:**
- `web/src/components/toolkits/discovery-results.tsx` — Show:
  - LLM-determined category with explanation
  - Component relationships (if detected)
  - Confidence indicators
  - "Analyzed by AI" badge vs "Pattern-matched" badge

- `web/src/lib/types.ts` — Add fields to `DiscoveredComponent`:
  - `relationship_group?: string` — which group/pipeline this belongs to
  - `entry_point?: boolean` — is this a primary user-facing component

---

## Task 5: Update Seeder for LLM Path

**What:** Seeder uses LLM analysis when Anthropic API key is available.

**Files to modify:**
- `controller/src/controller/toolkits/seeder.py` — If `LLMOnboarder` is available, use it instead of pattern-matching discovery. Fall back gracefully.

---

## Execution Order

```
Task 1 (RepoAnalyzer)    → git clone + context gathering
Task 2 (LLMOnboarder)    → prompt engineering + Claude API call + parser
Task 3 (Integration)     → wire into /discover endpoint
Task 4 (Frontend)        → show richer LLM-analyzed data
Task 5 (Seeder)          → use LLM path on startup
```

Tasks 1-2 are the core. Task 3 wires it in. Tasks 4-5 are polish.

## Dependencies

- `anthropic` Python package (needs to be added to controller Dockerfile)
- `git` binary in the controller container (needs to be installed)
