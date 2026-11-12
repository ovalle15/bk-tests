#!/usr/bin/env bash
# Shows which hosted macOS agent picked up the job (ties back to the architecture slides).
set -euo pipefail

echo "--- :apple: Hosted agent details"
sw_vers
echo "Architecture: $(uname -m)"
echo "CPU: $(sysctl -n machdep.cpu.brand_string)"
echo "Xcode: $(xcodebuild -version 2>/dev/null | sed -n 1p)"
echo "Python: $(python3 --version)"

buildkite-agent annotate --style info --context agent-info <<EOF
### :apple: Ran on a Buildkite hosted macOS agent
| Detail | Value |
|---|---|
| Queue | \`${BUILDKITE_AGENT_META_DATA_QUEUE:-macos-medium}\` |
| Agent | \`${BUILDKITE_AGENT_NAME}\` |
| macOS | $(sw_vers -productVersion) |
| Architecture | $(uname -m) |
| CPU | $(sysctl -n machdep.cpu.brand_string) |
| Xcode | $(xcodebuild -version 2>/dev/null | sed -n 1p) |

Each job gets a clean, ephemeral machine.
EOF
