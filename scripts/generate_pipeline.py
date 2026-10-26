"""Generate the queue-routed Buildkite pipeline as JSON."""

import json
import os

from queue_router import QueueRouter


def generate_pipeline(branch: str, router: QueueRouter) -> dict:
    # Main runs an extra suite; feature branches get faster feedback.
    suites = ["unit", "integration"]
    if branch in ["ao-deploy", "main", "ao-tests", "ao-cache"]:
        suites.append("e2e")

    steps = [
        {
            "label": ":satellite: Record Buildkite cluster",
            "key": "record-cluster",
            "agents": {"queue": router.resolve("record-cluster").queue},
            "command": (
                'buildkite-agent meta-data set "dd_tags.buildkite_cluster_id" '
                '"$BUILDKITE_CLUSTER_ID"\n'
                'buildkite-agent meta-data get "dd_tags.buildkite_cluster_id"'
            ),
        }, 
        {
            "label": ":cat: Validate queue routing",
            "key": "validate-queue-routing",
            "agents": {"queue": router.resolve("record-cluster").queue},
            "command": "python3 scripts/queue_router.py validate"
        }, 
        {
            "trigger": "ao-cache",
            "key": "ao-cache-trigger",
            "depends_on": ["validate-queue-routing"],
            "build": {
                # ao-cache builds a different repo (ao-bk-playground). Without
                # a branch, the trigger builds that repo's default branch (main).
                "branch": "ao-cache",
                "env": {"QUEUE": router.resolve_pipeline("ao-cache").queue},
            },
        },
    ]

    for suite in suites:
        steps.append(
            {
                "label": f":test_tube: {suite} tests (demo)",
                "key": f"test-{suite}",
                "agents": {"queue": router.resolve(suite).queue},
                "env": {"TEST_SUITE": suite},
                "command": 'echo "Demo: running $TEST_SUITE tests"',
                "depends_on": ["ao-cache-trigger"],
            }
        )

    steps.append(
        {
            "label": ":white_check_mark: Summary",
            "key": "summary",
            "agents": {"queue": router.resolve("summary").queue},
            "depends_on": [f"test-{suite}" for suite in suites],
            "command": 'echo "All generated demo test steps passed"',
        },
    )

    return {"steps": steps}


if __name__ == "__main__":
    # Keep stdout exclusively for pipeline JSON, not diagnostic messages.
    queue_router = QueueRouter.from_file()
    print(
        json.dumps(
            generate_pipeline(os.environ.get("BUILDKITE_BRANCH", ""), queue_router),
            indent=2,
        )
    )
