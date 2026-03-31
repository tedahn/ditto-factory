# Toolkit System — Issues & Next Steps

## Current Issues

### 1. Version should mirror source repo
- **Problem:** Version is an internal counter (always v1, increments on re-import). Has no relationship to the actual tool's version.
- **Fix:** Use the repo's release tag (GitHub Releases API), semver from package.json/pyproject.toml, or branch@SHA as the version identifier. Show `superpowers v5.0.6` not `superpowers v1`.

### 2. Category classification is meaningless
- **Problem:** Pattern-matching on directory names produces `mixed` for almost everything. Doesn't capture what the tool actually does.
- **Fix:** LLM reasoning during onboarding should classify the toolkit's purpose:
  - superpowers → "development methodology framework"
  - agency-agents → "agent persona library"
  - agent-reach → "capability extension (internet access)"
  - beads → "persistent memory system"
  - agent-os → "codebase standards injection"
  - ui-ux-pro-max → "design intelligence system"
- Categories should be semantic, not structural.

### 3. Components are a random sprawl
- **Problem:** Each directory becomes a flat component. No relationships, no execution context, no understanding of how components work together.
- **Fix:** The onboarding agent should identify:
  - **Component groups/pipelines:** brainstorming → writing-plans → executing-plans is a workflow, not 3 independent skills
  - **Support files vs primary components:** prompt templates, test files, reference docs are part of a component, not standalone
  - **Entry points vs internals:** which components are user-facing vs implementation details
  - **Dependencies between components:** subagent-driven-development depends on test-driven-development

### 4. Structure must support deterministic replication
- **Problem:** The imported data doesn't capture enough context for an agent to actually use the tool correctly in a headless run.
- **Fix:** The onboarding agent should extract:
  - **Trigger conditions:** when should each component activate (from YAML frontmatter, CLAUDE.md patterns)
  - **Load requirements:** what needs to be in .claude/skills/, what needs MCP config, what needs CLAUDE.md rules
  - **Execution context:** environment variables, dependencies, runtime requirements
  - **Usage patterns:** how the original tool expects to be invoked (slash commands, skill triggers, hooks)

## Root Cause

All 4 issues stem from the same problem: **dumb pattern matching can't understand tools.** The discovery engine treats repos as file trees, not as systems with intent, structure, and behavior.

## Solution: Agent-Driven Onboarding

Replace the current Python-based discovery engine with a **toolkit onboarding workflow** that uses ditto-factory's own agent system:

### The Onboarding Workflow

1. **Clone** — Agent clones the repo (zero API calls)
2. **Analyze** — Agent reads README, CLAUDE.md, package configs, directory structure
3. **Classify** — Agent determines: what is this tool? what category? what's its purpose?
4. **Map Components** — Agent identifies components, their relationships, and how they work together
5. **Extract Metadata** — Agent pulls version from releases/tags/config, maps trigger conditions, load requirements
6. **Produce Manifest** — Structured JSON manifest with all the above
7. **User Reviews** — Manifest shown in dashboard for approval
8. **Import** — Registry stores the agent-produced manifest

### Built-in Workflow

This should be a **built-in workflow template** that ships with ditto-factory:
- Task type: `toolkit_onboarding`
- Agent gets a skill that teaches it how to analyze tool repos
- The skill includes examples of correct classification for known repo types
- The agent produces a structured manifest as its output artifact

### Benefits
- Zero GitHub API token usage (git clone only)
- Correct classification via LLM reasoning
- Component relationships captured
- Version mapped from source
- Replication context extracted
- Dogfooding ditto-factory's own workflow engine
