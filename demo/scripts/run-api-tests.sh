#!/usr/bin/env bash
# Starts the Notes API, runs the API tests against it, and annotates the build.
set -uo pipefail

REPORT_DIR="demo/reports"
mkdir -p "$REPORT_DIR"
export API_PORT="${API_PORT:-8080}"
# Plain tracebacks: newer Python colorizes them, which shows as escape codes in annotations.
export PYTHON_COLORS=0 NO_COLOR=1

echo "--- :python: Starting Notes API on port $API_PORT"
python3 demo/api/server.py > "$REPORT_DIR/server.log" 2>&1 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 30); do
  if curl -fs "http://127.0.0.1:$API_PORT/health" > /dev/null; then
    echo "API is up"
    break
  fi
  sleep 0.5
done

echo "+++ :test_tube: Running API tests"
python3 demo/tests/run.py "$REPORT_DIR"
STATUS=$?

echo "--- :memo: Server log"
cat "$REPORT_DIR/server.log"

if command -v buildkite-agent > /dev/null; then
  if [[ $STATUS -eq 0 ]]; then
    STYLE=success
    NEXT=":unlock: **Next:** click the **:rocket: Deploy to staging?** step in the build graph, then click **Continue** to release."
  else
    STYLE=error
    NEXT=":lock: **Deploy to staging? is locked** because API tests failed. Fix the code and push. The new build unlocks the approval."
  fi
  printf '\n%s\n' "$NEXT" >> "$REPORT_DIR/summary.md"
  buildkite-agent annotate --style "$STYLE" --context api-tests < "$REPORT_DIR/summary.md"
fi

if [[ $STATUS -ne 0 ]]; then
  echo "^^^ +++"
  echo "API tests failed. See the annotation at the top of the build page."
fi
exit "$STATUS"
