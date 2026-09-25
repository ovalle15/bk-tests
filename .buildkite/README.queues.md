# Buildkite queue routing

The shared routing policy is `.buildkite/queue-routing.json`. It independently
controls queues for entire pipelines and for jobs generated inside `ao-tests`.

## Queue catalog

Every usable queue is declared once under `queues`:

```json
"queues": {
  "kubernetes": {
    "key": "kube",
    "type": "self_hosted",
    "environment_override": "BUILDKITE_QUEUE_KUBERNETES"
  },
  "macos-medium": {
    "key": "macos-med",
    "type": "hosted",
    "environment_override": "BUILDKITE_QUEUE_MACOS_MEDIUM"
  }
}
```

The object name, such as `kubernetes`, is a readable routing name. `key` must
match a queue that already exists in the pipeline's Buildkite cluster. `type`
documents whether Buildkite or the organization hosts the agents. Pipeline and
workload assignments may use either the readable name (`kubernetes`) or the
real Buildkite key (`kube`).

## Pipeline assignments

Assign each pipeline to one queue from the catalog:

```json
"pipelines": {
  "ao-tests": {
    "queue": "kubernetes"
  },
  "ao-deploy": {
    "queue": "kubernetes"
  }
}
```

The dynamic pipeline resolves `pipelines.ao-deploy` and passes its queue key to
the triggered build as `QUEUE`. The live `ao-deploy` bootstrap and its uploaded
deployment steps use `${QUEUE}`.

The initial `ao-tests` bootstrap cannot read this repository before Buildkite
schedules it. Keep its live Buildkite Steps configuration synchronized with
`pipelines.ao-tests`.

## Workload assignments

Generated command jobs are assigned independently:

```json
"workloads": {
  "unit": {
    "queue": "kubernetes"
  },
  "e2e": {
    "queue": "macos-medium"
  }
}
```

Changing a workload's visible Buildkite label does not change routing. The key
must match the name passed to `router.resolve_workload()` or `router.resolve()`.

## Validate and inspect routes

```bash
python3 scripts/queue_router.py validate
python3 scripts/queue_router.py list
python3 scripts/queue_router.py list --kind pipeline
python3 scripts/queue_router.py resolve --kind pipeline ao-deploy
python3 scripts/queue_router.py resolve e2e
```

Preview the complete dynamic pipeline:

```bash
BUILDKITE_BRANCH=main python3 scripts/generate_pipeline.py
```

## Temporary overrides

Target-specific overrides take priority over queue-catalog overrides:

```bash
# Only the ao-deploy pipeline
BUILDKITE_QUEUE_PIPELINE_AO_DEPLOY=kube \
  python3 scripts/queue_router.py resolve --kind pipeline ao-deploy

# Only the e2e workload
BUILDKITE_QUEUE_WORKLOAD_E2E=macos-lg \
  python3 scripts/queue_router.py resolve e2e

# Every pipeline and workload assigned to the kubernetes catalog entry
BUILDKITE_QUEUE_KUBERNETES=kube \
  python3 scripts/queue_router.py list
```

Overrides select an existing queue; they do not create queues. Permanent
routing changes belong in `queue-routing.json`.
