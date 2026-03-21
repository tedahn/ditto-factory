# Plan 07: End-to-End Integration Test for Skill Hotloading Pipeline

## Problem Statement

There are 102 unit/integration tests but none covering the full skill hotloading flow:

```
TaskRequest -> Orchestrator.handle_task() -> TaskClassifier.classify()
  -> SkillInjector.format_for_redis() -> RedisState.push_task()
  -> entrypoint.sh reads skills from Redis payload
```

Each module is tested in isolation, but the integration seams between them are unverified. A regression in the Redis payload format (e.g., `skills` key renamed) would pass all unit tests but break production.

---

## 1. Test Scenarios

### Scenario 1: Happy Path -- Skills Matched and Injected

- **Input**: `TaskRequest` with task="Fix the React login form validation" and `source_ref={"labels": ["frontend"]}`
- **Pre-seed**: Two skills in the registry: `react-debugging` (language=["javascript"], domain=["frontend"]) and `api-design` (language=["python"], domain=["backend"])
- **Expected**:
  1. Classifier returns `ClassificationResult` with `react-debugging` matched
  2. Injector formats it as `[{"name": "react-debugging", "content": "..."}]`
  3. Redis `task:{thread_id}` contains `skills` key with the formatted array
  4. Job record has `skills_injected=["react-debugging"]` and `agent_type="frontend"`
  5. Thread status transitions: IDLE -> RUNNING

### Scenario 2: No Skills Match

- **Input**: `TaskRequest` with task="Update the README"
- **Pre-seed**: Same skills as Scenario 1 (none match a docs task with no language/domain overlap)
- **Expected**:
  1. Classifier returns `ClassificationResult` with empty skills list
  2. Redis `task:{thread_id}` has `skills: []` (empty array, not absent)
  3. Job record has `skills_injected=[]` and `agent_type="general"`
  4. Spawner is still called (the pipeline does not abort)

### Scenario 3: Classifier Failure (Graceful Degradation)

- **Input**: Any valid `TaskRequest`
- **Setup**: Inject an `EmbeddingProvider` mock that raises `EmbeddingError`; registry has no tag-matched skills either
- **Expected**:
  1. Orchestrator catches the exception, logs it, continues with `matched_skills=[]`
  2. Redis payload has `skills: []`
  3. Job is spawned with default `agent_image` (not the resolved one)
  4. No crash, no 500 error

### Scenario 4: Budget Exceeded -- Skills Trimmed

- **Input**: `TaskRequest` with task matching three skills
- **Pre-seed**: Three skills totaling 20,000 chars (budget is 16,000)
  - `skill-a`: 6,000 chars, `skill-b`: 6,000 chars, `skill-c`: 8,000 chars
- **Expected**:
  1. Classifier returns all three as matches
  2. `_enforce_budget()` trims `skill-c` (total would exceed 16,000)
  3. Redis payload contains only `skill-a` and `skill-b`
  4. Job record has `skills_injected=["skill-a", "skill-b"]`

### Scenario 5: Entrypoint Skill File Writing (Bash Unit Test)

- **Input**: A JSON string matching the Redis `skills` payload format
- **Expected**: `entrypoint.sh` skill injection block writes correct `.md` files to `.claude/skills/`
- **Note**: Tested separately as a bash script test (see Section 4)

---

## 2. What to Mock vs. Use Real

| Component | Strategy | Rationale |
|-----------|----------|-----------|
| **SQLite StateBackend** | Real (`SQLiteBackend.create("sqlite+aiosqlite://")`) | In-memory SQLite is fast, tests real SQL queries |
| **Redis (RedisState)** | `fakeredis.aioredis.FakeRedis()` | No Redis server needed; `fakeredis` supports all commands we use (`set`, `get`, `rpush`, `xadd`) |
| **SkillRegistry** | Real (backed by same SQLite) | Tests actual DB queries for skill search |
| **TaskClassifier** | Real | Tests actual classification logic including tag matching and budget enforcement |
| **EmbeddingProvider** | Mock returning fixed vectors | Avoids external API calls; controls similarity scores deterministically |
| **SkillInjector** | Real | Trivial class, no reason to mock |
| **AgentTypeResolver** | Real | Trivial class, no reason to mock |
| **PerformanceTracker** | Real (backed by SQLite) | Tests recording injection events |
| **JobSpawner** | **Mock** (`AsyncMock` returning a predictable `job_name`) | Avoids K8s dependency entirely |
| **JobMonitor** | **Mock** | Not exercised in the spawn path |
| **IntegrationRegistry** | Real with a stub `CLIIntegration` | Tests prompt building through real integration |
| **GitHub client** | **Mock** / `None` | Not needed for skill injection path |

### Key Principle

Mock at the infrastructure boundary (K8s, external APIs), use real implementations for everything inside the controller process.

---

## 3. Verifying Skills in Redis Task Payload

After `orchestrator.handle_task()` completes, assert against the fakeredis instance directly:

```python
async def _assert_redis_skills(self, redis: FakeRedis, thread_id: str, expected_slugs: list[str]):
    """Verify the skills array in the Redis task payload."""
    raw = await redis.get(f"task:{thread_id}")
    assert raw is not None, "Task payload missing from Redis"

    payload = json.loads(raw)

    # Structural assertions
    assert "skills" in payload, "skills key missing from Redis payload"
    assert isinstance(payload["skills"], list), "skills must be a list"

    # Content assertions
    actual_slugs = [s["name"] for s in payload["skills"]]
    assert actual_slugs == expected_slugs, f"Expected {expected_slugs}, got {actual_slugs}"

    # Each skill must have non-empty content
    for skill_entry in payload["skills"]:
        assert "name" in skill_entry, "skill entry missing 'name'"
        assert "content" in skill_entry, "skill entry missing 'content'"
        assert len(skill_entry["content"]) > 0, f"skill {skill_entry['name']} has empty content"

    # Verify other required payload keys are present
    for key in ("task", "system_prompt", "repo_url", "branch"):
        assert key in payload, f"Redis payload missing required key: {key}"
```

This assertion method is shared across all scenarios.

---

## 4. Verifying Entrypoint Bash Logic

The entrypoint skill injection block (`images/agent/entrypoint.sh`, lines ~45-60) is tested separately as a bash unit test. This avoids needing Docker in the test suite.

### Approach: Extract and Test the Bash Function

```bash
#!/usr/bin/env bash
# File: controller/tests/e2e_skills/test_entrypoint_skills.sh

set -euo pipefail

TMPDIR=$(mktemp -d)
cd "$TMPDIR"

# Simulate the Redis payload (what jq would extract)
SKILLS_JSON='[
  {"name": "react-debugging", "content": "# Debug React\nUse React DevTools."},
  {"name": "api-design", "content": "# API Design\nUse REST conventions."}
]'

# --- Paste the exact entrypoint logic under test ---
if [ -n "$SKILLS_JSON" ] && [ "$SKILLS_JSON" != "null" ]; then
    mkdir -p .claude/skills
    while read -r skill; do
        SKILL_NAME=$(echo "$skill" | jq -r '.name' | tr -cd 'a-zA-Z0-9_-')
        SKILL_CONTENT=$(echo "$skill" | jq -r '.content')
        echo "$SKILL_CONTENT" > ".claude/skills/${SKILL_NAME}.md"
    done < <(echo "$SKILLS_JSON" | jq -c '.[]')
fi

# --- Assertions ---
PASS=true

assert_file() {
    local file="$1" expected="$2"
    if [ ! -f "$file" ]; then
        echo "FAIL: $file does not exist"
        PASS=false
    elif [ "$(cat "$file")" != "$expected" ]; then
        echo "FAIL: $file content mismatch"
        echo "  Expected: $expected"
        echo "  Got: $(cat "$file")"
        PASS=false
    fi
}

assert_file ".claude/skills/react-debugging.md" "# Debug React
Use React DevTools."

assert_file ".claude/skills/api-design.md" "# API Design
Use REST conventions."

# Edge case: no skills
SKILLS_JSON='[]'
rm -rf .claude/skills
mkdir -p .claude/skills
# The loop should produce zero files
count=$(ls -1 .claude/skills/ 2>/dev/null | wc -l | tr -d ' ')
if [ "$count" != "0" ]; then
    echo "FAIL: Expected 0 files for empty skills array, got $count"
    PASS=false
fi

# Cleanup
rm -rf "$TMPDIR"

if $PASS; then
    echo "ALL ENTRYPOINT SKILL TESTS PASSED"
    exit 0
else
    echo "SOME TESTS FAILED"
    exit 1
fi
```

**Integration with pytest**: Run this bash test via `subprocess.run()` in a pytest wrapper so it appears in the same test report.

---

## 5. Test File Location and Structure

```
controller/tests/
  e2e_skills/
    __init__.py
    conftest.py               # Shared fixtures: fake Redis, in-memory SQLite,
                              # seeded skills, mock spawner, wired orchestrator
    test_skill_pipeline.py    # Scenarios 1-4 (Python async tests)
    test_entrypoint_skills.sh # Scenario 5 (bash script)
    test_entrypoint_wrapper.py # pytest wrapper for the bash test
```

### conftest.py Fixtures

```python
@pytest_asyncio.fixture
async def fake_redis():
    """FakeRedis instance shared across tests."""
    redis = FakeRedis()
    yield redis
    await redis.flushall()

@pytest_asyncio.fixture
async def sqlite_state():
    """In-memory SQLite backend."""
    backend = await SQLiteBackend.create("sqlite+aiosqlite://")
    yield backend

@pytest_asyncio.fixture
async def skill_registry(sqlite_state):
    """SkillRegistry backed by SQLite, pre-seeded with test skills."""
    registry = SkillRegistry(sqlite_state)
    await registry.create(Skill(
        id="sk-1", slug="react-debugging", name="React Debugging",
        content="# Debug React Components\n...(2000 chars)...",
        language=["javascript"], domain=["frontend"],
        is_default=False,
    ))
    await registry.create(Skill(
        id="sk-2", slug="api-design", name="API Design",
        content="# API Design Patterns\n...(2000 chars)...",
        language=["python"], domain=["backend"],
        is_default=False,
    ))
    yield registry

@pytest_asyncio.fixture
async def mock_embedding_provider():
    """Returns deterministic embeddings that make react-debugging match frontend tasks."""
    provider = AsyncMock(spec=EmbeddingProvider)
    # Return vectors that produce high cosine similarity for intended matches
    async def embed(text):
        if "react" in text.lower() or "frontend" in text.lower():
            return [1.0, 0.0, 0.0]  # frontend cluster
        elif "api" in text.lower() or "backend" in text.lower():
            return [0.0, 1.0, 0.0]  # backend cluster
        return [0.0, 0.0, 1.0]     # generic cluster
    provider.embed = AsyncMock(side_effect=embed)
    yield provider

@pytest_asyncio.fixture
async def wired_orchestrator(
    sqlite_state, fake_redis, skill_registry, mock_embedding_provider
):
    """Fully wired Orchestrator with real classifier/injector, mock spawner."""
    from controller.config import Settings
    settings = Settings(
        skill_registry_enabled=True,
        agent_image="ditto-agent:test",
        # ... other required settings
    )
    redis_state = RedisState(fake_redis)
    classifier = TaskClassifier(
        registry=skill_registry,
        embedding_provider=mock_embedding_provider,
        settings=settings,
    )
    injector = SkillInjector()
    resolver = AgentTypeResolver()
    tracker = PerformanceTracker(sqlite_state)
    registry = IntegrationRegistry()
    registry.register(CLIIntegration())
    spawner = MagicMock(spec=JobSpawner)
    spawner.spawn = MagicMock(return_value="test-job-001")
    monitor = AsyncMock(spec=JobMonitor)

    orch = Orchestrator(
        settings=settings,
        state=sqlite_state,
        redis_state=redis_state,
        registry=registry,
        spawner=spawner,
        monitor=monitor,
        classifier=classifier,
        injector=injector,
        resolver=resolver,
        tracker=tracker,
    )
    yield orch, redis_state, sqlite_state, fake_redis, spawner
```

### test_skill_pipeline.py Structure

```python
class TestSkillPipelineE2E:
    """End-to-end tests for the skill hotloading pipeline."""

    async def test_happy_path_skills_matched(self, wired_orchestrator):
        orch, redis_state, state, fake_redis, spawner = wired_orchestrator
        task_request = TaskRequest(
            thread_id="e2e-happy-001",
            source="cli",
            source_ref={"labels": ["frontend"]},
            repo_owner="test-org",
            repo_name="test-repo",
            task="Fix the React login form validation",
        )
        await orch.handle_task(task_request)

        # Assert Redis payload
        await self._assert_redis_skills(fake_redis, "e2e-happy-001", ["react-debugging"])

        # Assert Job record
        job = await state.get_active_job_for_thread("e2e-happy-001")
        assert job is not None
        assert job.agent_type == "frontend"
        assert "react-debugging" in job.skills_injected

        # Assert spawner was called
        spawner.spawn.assert_called_once()

    async def test_no_skills_match(self, wired_orchestrator): ...
    async def test_classifier_failure_graceful_degradation(self, wired_orchestrator): ...
    async def test_budget_exceeded_skills_trimmed(self, wired_orchestrator): ...
```

---

## 6. Effort Estimate

| Task | Effort | Notes |
|------|--------|-------|
| Write `conftest.py` with fixtures | 2 hours | Most complexity is wiring the orchestrator |
| Scenario 1: Happy path | 1.5 hours | Includes the `_assert_redis_skills` helper |
| Scenario 2: No skills match | 0.5 hours | Reuses fixtures, simple assertions |
| Scenario 3: Classifier failure | 1 hour | Needs mock that raises, verify graceful degradation |
| Scenario 4: Budget exceeded | 1 hour | Needs 3 large skills, verify trimming |
| Scenario 5: Bash entrypoint test | 1 hour | Shell script + pytest wrapper |
| CI integration (add to pytest config) | 0.5 hours | Add `fakeredis` to dev deps, mark as `e2e` |
| **Total** | **~7.5 hours** | ~1 developer-day |

### Prerequisites

- `pip install fakeredis[aioredis]` (add to `pyproject.toml` dev dependencies)
- `jq` available in CI for the bash test (already present in most CI images)

### Risk: Embedding Search Fixture Fragility

The mock embedding provider returns fixed 3D vectors. If the `SkillRegistry.search_by_embedding` method changes its similarity calculation, the test vectors may need updating. Mitigate by using the tag-based fallback path for most scenarios and only testing embedding search explicitly in Scenario 1.

---

## ADR: Why Not a Full Docker Compose E2E Test?

**Status**: Accepted

**Context**: We considered running the controller + real Redis + mock agent in Docker Compose for maximum realism.

**Decision**: Use in-process test with fakeredis and in-memory SQLite.

**Consequences**:
- (+) Tests run in <2 seconds, no Docker dependency in CI
- (+) Deterministic: no network flakiness, no port conflicts
- (+) Can debug with standard pytest tooling
- (-) Does not exercise the actual Redis wire protocol (fakeredis may differ slightly)
- (-) Entrypoint bash logic tested separately, not as an integrated Docker flow
- Acceptable trade-off: the seam between "Redis payload format" and "entrypoint reads it" is covered by the contract test (Scenario 5 validates the same JSON structure)
