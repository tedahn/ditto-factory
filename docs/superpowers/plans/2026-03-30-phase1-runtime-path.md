# Phase 1: Runtime Path — Implementation Plan

**Goal:** Make imported toolkits actually usable by running agents. Bridge the gap from "toolkit in registry" to "agent uses it."

## Architecture Understanding

Current flow: Task → Orchestrator → Classifier selects Skills → Injector formats them → Spawner creates K8s pod → Agent runs

- **SkillInjector** formats skills as `[{name, content}]` for Redis storage
- **TaskClassifier** uses tag/embedding matching to find relevant skills
- **JobSpawner** creates K8s pod with env vars but NO file mounting today
- Skills reach the agent via Redis (prompt injection), NOT via filesystem

**Key insight:** We don't need K8s ConfigMaps yet for local Docker dev. The existing Redis skill injection path works. We just need toolkit components to flow through it.

---

## Task 1: Activation Bridge — Link ToolkitComponent to Skill System

**What:** When a toolkit is imported, automatically create `Skill` records from its components so the existing classifier/injector can find them.

**Files to modify:**
- `controller/src/controller/skills/models.py` — Add `source_toolkit_id` and `source_component_id` fields to the `Skill` dataclass (nullable, for tracking provenance)
- `controller/src/controller/toolkits/registry.py` — Add `activate_toolkit(toolkit_slug)` method that:
  1. Gets all components for the toolkit
  2. For each component of type SKILL or AGENT:
     - Creates a `Skill` record in the skills registry (if not already linked)
     - Sets `source_toolkit_id` and `source_component_id` on the skill
     - Content = primary file content from the component
     - Tags = component tags + toolkit tags
  3. Returns count of activated skills
- `controller/src/controller/toolkits/registry.py` — Add `deactivate_toolkit(toolkit_slug)` — removes linked skills
- `controller/src/controller/toolkits/api.py` — Add `POST /api/v1/toolkits/{slug}/activate` and `POST /api/v1/toolkits/{slug}/deactivate` endpoints

**Auto-activation:** In `import_from_manifest`, after importing, automatically activate if the toolkit has skill/agent components.

**DB change:** Add `source_toolkit_id TEXT` and `source_component_id TEXT` columns to the `skills` table.

**Acceptance:** After importing superpowers, its skill components appear in the skills registry. The classifier can find them for matching tasks.

---

## Task 2: AgentLoadout Model

**What:** A data model that defines the complete environment for an agent pod.

**Files to create:**
- `controller/src/controller/loadout.py`

```python
@dataclass
class AgentLoadout:
    """Complete environment specification for an agent pod."""
    thread_id: str
    
    # Skills to inject (via existing Redis path)
    skills: list[dict]  # [{name, content}] — same format as SkillInjector
    
    # Files to mount in the agent's workspace
    mounted_files: dict[str, str]  # {relative_path: content}
    # e.g. {".claude/skills/tdd/SKILL.md": "...", ".claude/rules/standards.md": "..."}
    
    # MCP config entries to merge
    mcp_config: dict  # merged into agent's mcp.json
    
    # Extra environment variables
    env_vars: dict[str, str]
    
    # CLAUDE.md additions
    claude_md_additions: list[str]  # appended to the agent's CLAUDE.md
```

**Files to create:**
- `controller/src/controller/loadout_builder.py`

```python
class LoadoutBuilder:
    """Builds an AgentLoadout from task context + toolkit components."""
    
    def __init__(self, toolkit_registry, skill_injector, settings):
        ...
    
    async def build(self, thread_id, task_description, 
                    explicit_toolkit_slugs=None,
                    explicit_component_slugs=None,
                    classification_result=None) -> AgentLoadout:
        """Build loadout for a task.
        
        1. Start with classified skills (existing path)
        2. Add explicitly requested toolkit components
        3. For each component, determine mount strategy:
           - SKILL components → add to skills list AND mount files
           - PROFILE components → add to claude_md_additions
           - PLUGIN components → add to mcp_config
           - TOOL components → add to env_vars + mcp_config
        4. Enforce budgets (max chars, max files)
        """
```

**Acceptance:** LoadoutBuilder can produce a loadout from a mix of classified skills and explicit toolkit selections.

---

## Task 3: Wire Loadout into Orchestrator

**What:** Update the orchestrator to use LoadoutBuilder when processing tasks.

**Files to modify:**
- `controller/src/controller/orchestrator.py` — In the task processing flow:
  1. After classification, build a LoadoutBuilder
  2. Pass classification result + any explicit toolkit selections from the task request
  3. Get AgentLoadout back
  4. Pass loadout to spawner (skills go via Redis as before, mounted_files are new)
  
- `controller/src/controller/models.py` — Add `toolkit_slugs: list[str] = []` and `component_slugs: list[str] = []` to `TaskRequest` so users can explicitly request toolkits

**Acceptance:** A task submitted with `toolkit_slugs=["superpowers"]` gets superpowers skills in its loadout.

---

## Task 4: Mount Files in Agent Environment

**What:** The spawner writes loadout files to the agent's workspace.

**For Docker (local dev):**
- Write mounted_files to a temp directory
- Mount as a Docker volume into the agent container
- Agent sees files at `.claude/skills/`, `.claude/rules/`, etc.

**For K8s (production):**
- Create a ConfigMap from mounted_files
- Mount ConfigMap as volumes in the pod spec

**Files to modify:**
- `controller/src/controller/jobs/spawner.py` — Accept `AgentLoadout` in `build_job_spec`:
  - Add mounted_files as ConfigMap volume mounts
  - Add extra env_vars to container env
  - Merge mcp_config into agent's mcp.json (via another ConfigMap or env var)

**Acceptance:** An agent pod with loadout containing superpowers skills has the SKILL.md files available in its `.claude/skills/` directory.

---

## Task 5: Frontend — Toolkit Selection on Task Submission

**What:** Let users pick toolkits when submitting a task.

**Files to modify:**
- `web/src/app/tasks/new/page.tsx` or `web/src/components/dashboard/quick-submit.tsx` — Add a "Toolkits" multi-select picker
- `web/src/lib/types.ts` — Add `toolkit_slugs` and `component_slugs` to task submission types
- `web/src/lib/hooks.ts` — Update `useSubmitTask` to include toolkit selections

**Design:**
- Below the task description input, add "Attach Toolkits" section
- Shows available toolkits as selectable chips/cards
- Selected toolkits highlighted
- Expand a toolkit to select specific components

**Acceptance:** User can submit a task with specific toolkits attached. The loadout builder uses them.

---

## Task 6: Frontend — Activate/Deactivate Controls

**What:** Add activate/deactivate buttons to toolkit detail page.

**Files to modify:**
- `web/src/app/toolkits/[slug]/page.tsx` — Add "Activate" / "Deactivate" toggle button
- `web/src/lib/hooks.ts` — Add `useActivateToolkit` and `useDeactivateToolkit` mutations
- Show activation status on toolkit list page (active = green dot)

**Acceptance:** User can activate a toolkit from the detail page. Activated toolkits show in the skills registry.
