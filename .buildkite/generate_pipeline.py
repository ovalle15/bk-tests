"""Print a Buildkite pipeline as JSON using only Python's standard library."""

import json
import os




def generate_pipeline(branch):
    # Main runs an extra suite; feature branches get faster feedback.
    suites = ["unit", "integration"]
    if branch == "main":
        suites.append("e2e")


    steps = []

    steps.append({
                "label":":satellite: Record Buildkite cluster",
                "key": "record-cluster",
                "command": 'buildkite-agent meta-data set \
                    "dd_tags.buildkite_cluster_id" \
                    "$BUILDKITE_CLUSTER_ID"',
            })
            
    for suite in suites:
        steps.append({
            "label": f":test_tube: {suite} tests (demo)",
            "key": f"test-{suite}",
            "env": {"TEST_SUITE": suite},
            # --no-interpolation preserves $TEST_SUITE for this job's shell.
            # Replace this echo with your real test runner.
            "command": 'echo "Demo: running $TEST_SUITE tests"',
        })

    steps.append(
        {
            "label": ":white_check_mark: Summary",
            "key": "summary",
            "depends_on": [f"test-{suite}" for suite in suites],
            "command": 'echo "All generated demo test steps passed"',
        },
    )

    return {"steps": steps}


if __name__ == "__main__":
    # Keep stdout exclusively for pipeline JSON, not diagnostic messages.
    print(json.dumps(generate_pipeline(os.environ.get("BUILDKITE_BRANCH", "")), indent=2))
