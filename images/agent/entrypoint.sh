#!/usr/bin/env bash
set -euo pipefail

# Required environment variables
: "${THREAD_ID:?THREAD_ID is required}"
: "${REDIS_URL:?REDIS_URL is required}"
: "${GITHUB_TOKEN:?GITHUB_TOKEN is required}"
: "${ANTHROPIC_API_KEY:?ANTHROPIC_API_KEY is required}"

REDIS_HOST=$(echo "$REDIS_URL" | sed 's|redis://||' | cut -d: -f1)
REDIS_PORT=$(echo "$REDIS_URL" | sed 's|redis://||' | cut -d: -f2)

cleanup() {
    local exit_code=$?
    echo "Agent exiting with code $exit_code"
    if [ -z "${RESULT_PUBLISHED:-}" ]; then
        redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" SET "result:$THREAD_ID" \
            "$(jq -n --arg branch "${BRANCH:-unknown}" --arg exit_code "$exit_code" --arg stderr "${STDERR:-agent crashed}" \
            '{branch: $branch, exit_code: ($exit_code | tonumber), commit_count: 0, stderr: $stderr}')" EX 3600
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

# Run Claude Code
CLAUDE_ARGS=(-p "$TASK" --allowedTools '*' --mcp-config /etc/df/mcp.json)
if [ -n "${SYSTEM_PROMPT:-}" ]; then
    CLAUDE_ARGS+=(--system-prompt "$SYSTEM_PROMPT")
fi

STDERR_FILE=$(mktemp)
set +e
claude "${CLAUDE_ARGS[@]}" 2>"$STDERR_FILE"
EXIT_CODE=$?
set -e
STDERR=$(cat "$STDERR_FILE")

# Count commits
COMMIT_COUNT=$(git rev-list --count HEAD ^origin/main 2>/dev/null || echo "0")

# Push branch
git push origin "$BRANCH" --force-with-lease 2>/dev/null || true

# Publish result
redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" SET "result:$THREAD_ID" \
    "$(jq -n --arg branch "$BRANCH" --arg exit_code "$EXIT_CODE" --arg commit_count "$COMMIT_COUNT" --arg stderr "$STDERR" \
    '{branch: $branch, exit_code: ($exit_code | tonumber), commit_count: ($commit_count | tonumber), stderr: $stderr}')" EX 3600
RESULT_PUBLISHED=1

echo "Agent completed: branch=$BRANCH exit_code=$EXIT_CODE commits=$COMMIT_COUNT"
