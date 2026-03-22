#!/usr/bin/env bash
set -euo pipefail

# Required environment variables
: "${THREAD_ID:?THREAD_ID is required}"
: "${REDIS_URL:?REDIS_URL is required}"
: "${GITHUB_TOKEN:?GITHUB_TOKEN is required}"
: "${ANTHROPIC_API_KEY:?ANTHROPIC_API_KEY is required}"

REDIS_HOST=$(echo "$REDIS_URL" | sed 's|redis://||' | cut -d: -f1)
REDIS_PORT=$(echo "$REDIS_URL" | sed 's|redis://||' | cut -d: -f2)

# === Tracing helpers ===
TRACE_EVENTS=""
AGENT_START_NS=$(date +%s%N 2>/dev/null || echo "0")

safe_trace() {
    # Append a trace event — never fail
    local event_type="$1"
    local timestamp
    timestamp=$(date -u +"%Y-%m-%dT%H:%M:%S.%3NZ" 2>/dev/null || date -u +"%Y-%m-%dT%H:%M:%SZ")
    local details="${2:-\"\"}"
    local entry
    entry=$(printf '{"type":"%s","timestamp":"%s","details":%s}' \
        "$event_type" "$timestamp" "$details" 2>/dev/null) || return 0
    if [ -n "$TRACE_EVENTS" ]; then
        TRACE_EVENTS="${TRACE_EVENTS},${entry}"
    else
        TRACE_EVENTS="${entry}"
    fi
}

safe_trace "AGENT_STARTED" "$(jq -n --arg tid "$THREAD_ID" '{thread_id: $tid}' 2>/dev/null || echo '""')"
# === End tracing helpers ===

cleanup() {
    local exit_code=$?
    echo "Agent exiting with code $exit_code"
    # Emit final trace event on crash path
    safe_trace "AGENT_COMPLETED" "$(jq -n \
        --argjson exit_code "$exit_code" \
        --arg reason "cleanup_trap" \
        '{exit_code: $exit_code, reason: $reason}' 2>/dev/null || echo '""')"
    if [ -z "${RESULT_PUBLISHED:-}" ]; then
        redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" SET "result:$THREAD_ID" \
            "$(jq -n --arg branch "${BRANCH:-unknown}" --arg exit_code "$exit_code" --arg stderr "${STDERR:-agent crashed}" \
            --argjson trace_events "[${TRACE_EVENTS}]" \
            '{branch: $branch, exit_code: ($exit_code | tonumber), commit_count: 0, stderr: $stderr, trace_events: $trace_events}')" EX 3600
    fi
}
trap cleanup EXIT

# Fetch task from Redis
TASK_JSON=$(redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" GET "task:$THREAD_ID")
if [ -z "$TASK_JSON" ] || [ "$TASK_JSON" = "(nil)" ]; then
    echo "No task found for thread $THREAD_ID"
    exit 1
fi

REPO_URL=$(echo "$TASK_JSON" | jq -r '.repo_url')
BRANCH=$(echo "$TASK_JSON" | jq -r '.branch')
TASK=$(echo "$TASK_JSON" | jq -r '.task')
SYSTEM_PROMPT=$(echo "$TASK_JSON" | jq -r '.system_prompt // empty')

# Read trace context from Redis payload (propagated by orchestrator)
TRACE_ID=$(echo "$TASK_JSON" | jq -r '.trace_id // empty' 2>/dev/null || echo "")
PARENT_SPAN_ID=$(echo "$TASK_JSON" | jq -r '.parent_span_id // empty' 2>/dev/null || echo "")

safe_trace "TASK_RECEIVED" "$(jq -n \
    --arg task "$(echo "$TASK" | head -c 200)" \
    --arg trace_id "$TRACE_ID" \
    '{task_preview: $task, trace_id: $trace_id}' 2>/dev/null || echo '""')"

# Clone and setup
git config --global credential.helper '!f() { echo "password=$GITHUB_TOKEN"; }; f'
git config --global user.name "Ditto Factory"
git config --global user.email "aal@noreply.github.com"

WORKSPACE="/workspace"
git clone "https://x-access-token:${GITHUB_TOKEN}@${REPO_URL#https://}" "$WORKSPACE"
cd "$WORKSPACE"

# Create or checkout branch
if git ls-remote --heads origin "$BRANCH" | grep -q "$BRANCH"; then
    git checkout "$BRANCH"
else
    git checkout -b "$BRANCH"
fi

# === Inject skills from task payload ===
SKILLS_JSON=$(echo "$TASK_JSON" | jq -r '.skills // empty')
if [ -n "$SKILLS_JSON" ] && [ "$SKILLS_JSON" != "null" ]; then
    mkdir -p .claude/skills
    while read -r skill; do
        SKILL_NAME=$(echo "$skill" | jq -r '.name' | tr -cd 'a-zA-Z0-9_-')
        if [ -z "$SKILL_NAME" ]; then
            echo "WARNING: Skipping skill with empty/invalid name"
            continue
        fi
        SKILL_CONTENT=$(echo "$skill" | jq -r '.content')
        echo "$SKILL_CONTENT" > ".claude/skills/${SKILL_NAME}.md"
    done < <(echo "$SKILLS_JSON" | jq -c '.[]')
    SKILL_COUNT=$(echo "$SKILLS_JSON" | jq length)
    echo "Injected ${SKILL_COUNT} skills into .claude/skills/"
    SKILL_NAMES=$(echo "$SKILLS_JSON" | jq -r '.[].name' 2>/dev/null | paste -sd ',' - || echo "")
    safe_trace "SKILLS_WRITTEN" "$(jq -n \
        --argjson count "${SKILL_COUNT}" \
        --arg names "$SKILL_NAMES" \
        '{count: $count, names: $names}' 2>/dev/null || echo '""')"
else
    echo "No skills to inject"
fi
# === End skill injection ===

# === Inject gateway MCP config if provided ===
GATEWAY_MCP=$(echo "$TASK_JSON" | jq -r '.gateway_mcp // empty')
if [ -n "$GATEWAY_MCP" ] && [ "$GATEWAY_MCP" != "null" ]; then
    # Merge gateway config with base mcp.json
    jq -s '.[0] * {mcpServers: (.[0].mcpServers + .[1])}' \
        /etc/df/mcp.json <(echo "$GATEWAY_MCP") > /tmp/mcp-merged.json
    MCP_CONFIG="/tmp/mcp-merged.json"
    echo "Injected gateway MCP config"
else
    MCP_CONFIG="/etc/df/mcp.json"
fi
# === End gateway MCP injection ===

# Run Claude Code
CLAUDE_ARGS=(-p "$TASK" --allowedTools '*' --mcp-config "$MCP_CONFIG")
if [ -n "${SYSTEM_PROMPT:-}" ]; then
    CLAUDE_ARGS+=(--system-prompt "$SYSTEM_PROMPT")
fi

CLAUDE_START_NS=$(date +%s%N 2>/dev/null || echo "0")
safe_trace "CLAUDE_STARTED" '""'

STDERR_FILE=$(mktemp)
set +e
claude "${CLAUDE_ARGS[@]}" 2>"$STDERR_FILE"
EXIT_CODE=$?
set -e
STDERR=$(cat "$STDERR_FILE")

CLAUDE_END_NS=$(date +%s%N 2>/dev/null || echo "0")
CLAUDE_DURATION_MS=$(( (CLAUDE_END_NS - CLAUDE_START_NS) / 1000000 )) 2>/dev/null || CLAUDE_DURATION_MS=0
safe_trace "CLAUDE_COMPLETED" "$(jq -n \
    --argjson exit_code "$EXIT_CODE" \
    --argjson duration_ms "$CLAUDE_DURATION_MS" \
    '{exit_code: $exit_code, duration_ms: $duration_ms}' 2>/dev/null || echo '""')"

# Count commits
COMMIT_COUNT=$(git rev-list --count HEAD ^origin/main 2>/dev/null || echo "0")

# Capture git activity
if [ "$COMMIT_COUNT" -gt 0 ] 2>/dev/null; then
    DIFF_STAT=$(git diff --shortstat "HEAD~${COMMIT_COUNT}" HEAD 2>/dev/null || echo "")
    COMMIT_MSGS=$(git log --oneline -n "$COMMIT_COUNT" 2>/dev/null | head -5 || echo "")
    safe_trace "GIT_ACTIVITY" "$(jq -n \
        --argjson count "$COMMIT_COUNT" \
        --arg diff "$DIFF_STAT" \
        --arg commits "$COMMIT_MSGS" \
        '{commit_count: $count, diff_stat: $diff, commit_messages: $commits}' 2>/dev/null || echo '""')"
fi

# Push branch
git push origin "$BRANCH" --force-with-lease 2>/dev/null || true

# Compute total duration
AGENT_END_NS=$(date +%s%N 2>/dev/null || echo "0")
TOTAL_DURATION_MS=$(( (AGENT_END_NS - AGENT_START_NS) / 1000000 )) 2>/dev/null || TOTAL_DURATION_MS=0
safe_trace "AGENT_COMPLETED" "$(jq -n \
    --argjson exit_code "$EXIT_CODE" \
    --argjson commit_count "$COMMIT_COUNT" \
    --argjson duration_ms "$TOTAL_DURATION_MS" \
    '{exit_code: $exit_code, commit_count: $commit_count, total_duration_ms: $duration_ms}' 2>/dev/null || echo '""')"

# Publish result (with trace events)
redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" SET "result:$THREAD_ID" \
    "$(jq -n --arg branch "$BRANCH" --arg exit_code "$EXIT_CODE" --arg commit_count "$COMMIT_COUNT" --arg stderr "$STDERR" \
    --argjson trace_events "[${TRACE_EVENTS}]" \
    '{branch: $branch, exit_code: ($exit_code | tonumber), commit_count: ($commit_count | tonumber), stderr: $stderr, trace_events: $trace_events}')" EX 3600
RESULT_PUBLISHED=1

echo "Agent completed: branch=$BRANCH exit_code=$EXIT_CODE commits=$COMMIT_COUNT"
