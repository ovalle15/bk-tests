#!/usr/bin/env bash
# Simulated staging deploy: fetches the build artifact from an earlier job and "releases" it.
set -euo pipefail

echo "--- :package: Downloading artifact from the Build step"
buildkite-agent artifact download "dist/notes-api.tar.gz" .
ls -lh dist/notes-api.tar.gz
SHA=$(shasum -a 256 dist/notes-api.tar.gz | cut -c1-12)

echo "--- :rocket: Deploying to staging (simulated)"
for step in "Uploading release" "Running smoke check" "Switching traffic"; do
  echo "$step..."
  sleep 2
done

buildkite-agent annotate --style success --context deploy <<EOF
### :rocket: Deployed to staging
Artifact \`notes-api.tar.gz\` (sha256 \`$SHA\`) from build #${BUILDKITE_BUILD_NUMBER}, commit \`${BUILDKITE_COMMIT:0:7}\`.
Approved by: ${BUILDKITE_UNBLOCKER:-n/a}
EOF
