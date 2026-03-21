#!/bin/bash
# images/mock-agent/entrypoint.sh
# Lightweight mock that simulates the real agent's Redis protocol.
set -euo pipefail

# ── Required env vars (same as real agent) ──
: "${THREAD_ID:?}"
: "${REDIS_URL:?}"
: "${GITHUB_TOKEN:?}"

# ── Configuration (injected by test) ──
MOCK_EXIT_CODE="${MOCK_EXIT_CODE:-0}"
MOCK_COMMIT_COUNT="${MOCK_COMMIT_COUNT:-1}"
MOCK_DELAY_SECONDS="${MOCK_DELAY_SECONDS:-2}"
MOCK_FAIL_PHASE="${MOCK_FAIL_PHASE:-}"  # "clone" | "push" | "result"

echo "[mock-agent] Starting for thread: $THREAD_ID"

# ── Step 1: Read task from Redis ──
TASK_JSON=$(redis-cli -u "$REDIS_URL" GET "task:$THREAD_ID")
if [ -z "$TASK_JSON" ]; then
    echo "[mock-agent] ERROR: No task found in Redis for $THREAD_ID"
    exit 1
fi

REPO_URL=$(echo "$TASK_JSON" | jq -r '.repo_url')
BRANCH=$(echo "$TASK_JSON" | jq -r '.branch')
TASK_TEXT=$(echo "$TASK_JSON" | jq -r '.task')

echo "[mock-agent] Task: $TASK_TEXT"
echo "[mock-agent] Repo: $REPO_URL Branch: $BRANCH"

# ── Step 2: Simulate failure if configured ──
if [ "$MOCK_FAIL_PHASE" = "clone" ]; then
    echo "[mock-agent] Simulating clone failure"
    exit 1
fi

# ── Step 3: Clone repo ──
mkdir -p /tmp/workspace
cd /tmp/workspace

# Use GitHub token for authenticated clone/push
AUTH_URL=$(echo "$REPO_URL" | sed "s|https://|https://x-access-token:${GITHUB_TOKEN}@|")
git clone "$AUTH_URL" repo 2>&1 || {
    echo "[mock-agent] Clone failed"
    exit 1
}
cd repo
git config user.email "mock-agent@ditto-factory.test"
git config user.name "Mock Agent"

git checkout -b "$BRANCH" 2>/dev/null || git checkout "$BRANCH"

# ── Step 4: Make trivial commits ──
ACTUAL_COMMITS=0
if [ "$MOCK_COMMIT_COUNT" -gt 0 ]; then
    for i in $(seq 1 "$MOCK_COMMIT_COUNT"); do
        echo "change-$i from mock agent (thread: $THREAD_ID)" > "file-$i.txt"
        git add "file-$i.txt"
        git commit -m "Mock commit $i for $THREAD_ID"
        ACTUAL_COMMITS=$((ACTUAL_COMMITS + 1))
    done
fi

echo "[mock-agent] Made $ACTUAL_COMMITS commits"

# ── Step 5: Push to remote ──
if [ "$MOCK_FAIL_PHASE" = "push" ]; then
    echo "[mock-agent] Simulating push failure"
    MOCK_EXIT_CODE=1
elif [ "$ACTUAL_COMMITS" -gt 0 ]; then
    echo "[mock-agent] Pushing branch $BRANCH to remote"
    git push origin "$BRANCH" 2>&1 || {
        echo "[mock-agent] Push failed"
        MOCK_EXIT_CODE=1
    }
fi

# ── Step 6: Simulate processing delay ──
if [ "$MOCK_DELAY_SECONDS" -gt 0 ]; then
    echo "[mock-agent] Sleeping ${MOCK_DELAY_SECONDS}s to simulate work"
    sleep "$MOCK_DELAY_SECONDS"
fi

# ── Step 7: Write result to Redis ──
if [ "$MOCK_FAIL_PHASE" = "result" ]; then
    echo "[mock-agent] Simulating result write failure -- exiting without writing"
    exit 1
fi

RESULT_JSON=$(jq -n \
    --arg branch "$BRANCH" \
    --argjson exit_code "$MOCK_EXIT_CODE" \
    --argjson commit_count "$ACTUAL_COMMITS" \
    --arg stderr "" \
    '{branch: $branch, exit_code: $exit_code, commit_count: $commit_count, stderr: $stderr}')

redis-cli -u "$REDIS_URL" SET "result:$THREAD_ID" "$RESULT_JSON" EX 3600

echo "[mock-agent] Result written. Exit code: $MOCK_EXIT_CODE"
exit "$MOCK_EXIT_CODE"
