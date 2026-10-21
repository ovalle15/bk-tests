# bk-tests

Buildkite pipelines that run on a local Kubernetes cluster, with traces, logs
and metrics from every job sent to Grafana Cloud.

```
Buildkite (queue: kube)
   │  jobs
   ▼
agent-stack-k8s controller ── creates one pod per job ──▶ job pod
   (namespace buildkite)                                    │ traces + logs (OTLP)
        ▲                                                   ▼
        └───────── scraped for metrics ─────────── Alloy, k8s-monitoring (namespace monitoring)
                                                     │  also collects cluster metrics,
                                                     │  Kubernetes events and object changes
                                                     ▼
                                           Grafana Cloud: Tempo, Loki, Mimir
                                                     │
                                                     ▼
                                    Buildkite + Kubernetes Monitoring dashboards
```

This README covers the whole setup in order. The details for each part live
next to its config:

| Part | Config | Details |
|------|--------|---------|
| Job runner | `helm/values.yaml`, `helm/otel-env.yaml` | `helm/README.md` |
| Cluster monitoring and Grafana Cloud | `observability/k8s-monitoring-values.yaml` | `observability/README.md` |
| Dashboards | `observability/dashboards/` | `observability/README.md` |
| Build step (`make build`) | `Makefile`, `.buildkite/pipeline.annotation.yaml` | Comments in those files |
| Queue routing | `.buildkite/queue-routing.json` | `.buildkite/README.queues.md` |

## What you need

- **A Kubernetes cluster** and `kubectl` pointed at it. This repo uses Rancher
  Desktop (context `rancher-desktop`).
- **Helm 3.**
- **A Buildkite organization** with a cluster and permission to create queues
  and agent tokens.
- **A Grafana Cloud stack** with Kubernetes Monitoring and Fleet Management.

Versions this setup was built and tested with:

| Component | Version |
|-----------|---------|
| agent-stack-k8s chart | 0.50.0 |
| Buildkite agent in job pods | 4.0.3 (the controller's default image) |
| grafana/k8s-monitoring chart | 4.5.3 |
| Grafana Alloy | 1.20.0 |

## 1. Buildkite: create the queue and an agent token

1. In Buildkite, open your cluster (here `test-cluster`) and create a
   **self-hosted** queue with the key `kube`.
2. On the cluster's **Agent Tokens** page, create a token for the controller.
   Keep it out of the repo; you'll pass it to Helm in step 3.
3. Point pipeline steps at the queue. `command` is whatever the step runs; for
   example, this repo's queue-routing check:

   ```yaml
   steps:
     - label: ":cat: Validate queue routing"
       command: python3 scripts/queue_router.py validate
       agents:
         queue: kube
   ```

## 2. Grafana Cloud: install Kubernetes monitoring

This uses Grafana Cloud's supported setup, the `grafana/k8s-monitoring` Helm
chart. It runs Grafana Alloy on each node, collects cluster metrics, Kubernetes
events and object changes, and receives OTLP from the Buildkite job pods.

1. In Grafana Cloud, start the Kubernetes Monitoring onboarding for your
   cluster. It gives you a `helm upgrade --install grafana-cloud …` snippet with
   a Fleet Management URL, your stack's instance ID and a token. This repo's
   `observability/k8s-monitoring-values.yaml` is that snippet as a values file,
   plus an OTLP receiver for Buildkite. If your stack isn't instance `1884458`
   in `prod-us-east-2` with Fleet Management at `fleet-management-prod-008`,
   update those values first.
2. Store the instance ID and token in a Secret instead of passing the token on
   the command line:

   ```sh
   kubectl create namespace monitoring

   read -rs GRAFANA_CLOUD_TOKEN   # paste the token; it isn't echoed
   kubectl -n monitoring create secret generic grafana-cloud-remote-cfg \
     --from-literal=username=<instance id> \
     --from-literal=password="$GRAFANA_CLOUD_TOKEN"
   unset GRAFANA_CLOUD_TOKEN
   ```

3. Install the chart:

   ```sh
   helm repo add grafana https://grafana.github.io/helm-charts
   helm repo update grafana
   helm upgrade --install grafana-cloud grafana/k8s-monitoring --version 4.5.3 \
     --namespace monitoring \
     --values observability/k8s-monitoring-values.yaml
   ```

4. Finish the Kubernetes Monitoring setup in Grafana Cloud. That creates the
   Fleet Management pipelines for cluster metrics and Kubernetes events, which
   Alloy picks up within 30 seconds.

**Check:** all pods in `monitoring` are `Running`, and Alloy has loaded its
remote configuration and opened the OTLP ports:

```sh
kubectl -n monitoring get pods
kubectl -n monitoring logs ds/grafana-cloud-alloy -c alloy \
  | grep -E 'successfully loaded remote configuration|Starting (GRPC|HTTP) server'
```

## 3. Kubernetes: install the agent-stack-k8s controller

The controller polls the `kube` queue and creates a Kubernetes Job for each
Buildkite job, so no agent pods run between builds.

```sh
kubectl create namespace buildkite

# Job pods run as this service account (set in helm/values.yaml).
kubectl -n buildkite create serviceaccount buildkite-agent

# OTLP endpoint for job pods (Alloy). Apply it before the controller creates any jobs.
kubectl apply -f helm/otel-env.yaml

read -rs BUILDKITE_AGENT_TOKEN   # paste the cluster agent token; it isn't echoed
helm upgrade --install agent-stack-k8s oci://ghcr.io/buildkite/helm/agent-stack-k8s \
  --namespace buildkite \
  --version 0.50.0 \
  --set agentToken="$BUILDKITE_AGENT_TOKEN" \
  --values helm/values.yaml
unset BUILDKITE_AGENT_TOKEN
```

For later upgrades, use `--reuse-values` instead of `--set agentToken` to keep
the token already stored on the release.

`helm/values.yaml` does more than set the queue:

| Setting | Effect |
|---------|--------|
| `BUILDKITE_OPENTELEMETRY_TRACING=true` | The agent records a span for each job phase. |
| `BUILDKITE_JOB_LOGS_OTLP=true` | Each line of job output is sent as a log record. |
| `BUILDKITE_TELEMETRY_SERVICE_NAME=buildkite-agent` | Sets `service.name` on everything the agent sends. |
| `envFrom: buildkite-otel-env` | Sends to Alloy at `grafana-cloud-otel-receiver.monitoring.svc.cluster.local:4317`. |
| `k8s.grafana.com/scrape` annotations | Alloy scrapes the controller's metrics on port 9216 as `job="buildkite/agent-stack-k8s"`. |

Job pods run agent v4, which only reads `BUILDKITE_OPENTELEMETRY_TRACING`. The
controller's own `agent-config.tracing-backend` option sets the older v3
variable, so don't rely on it.

Steps that deploy to the cluster (the `nasa-image` app) need RBAC for the
`buildkite-agent` service account in the target namespace. Here it's bound to
the `buildkite-nasa-image-deployer` Role in `nasa-image`.

**Check:** `kubectl -n buildkite get pods` shows `agent-stack-k8s-…` as
`Running`, and the controller's log contains `Starting controller … queue=kube`:

```sh
kubectl -n buildkite logs deploy/agent-stack-k8s | grep 'Starting controller'
```

## 4. Run a build and find it in Grafana

1. Run any pipeline whose steps use `queue: kube`. While it runs,
   `kubectl -n buildkite get pods` shows a `buildkite-<job id>-…` pod per job.
2. In Grafana Cloud, open **Explore** and look for the build. Controller
   metrics show up within a minute even without a build.

| Signal | Data source | Query |
|--------|-------------|-------|
| Metrics | `grafanacloud-<stack>-prom` | `buildkite_limiter_max_in_flight` (returns your `max-in-flight`, 10) |
| Traces | `grafanacloud-<stack>-traces` | `{ resource.buildkite.pipeline = "<pipeline slug>" }` |
| Logs | `grafanacloud-<stack>-logs` | `{service_name="buildkite-agent"} \| buildkite_pipeline_slug="<pipeline slug>"` |

Each job produces two traces, one from the checkout container and one from the
command container. Both carry the same `buildkite.job_id`. The cluster itself
(nodes, pods, events) is in Grafana Cloud's **Kubernetes Monitoring** app.

## 5. Grafana Cloud: import the dashboards

Import each file from `observability/dashboards/` under **Dashboards → New →
Import**:

| File | Dashboard |
|------|-----------|
| `buildkite-ci.json` | Buildkite CI: jobs, failures and where job time goes |
| `buildkite-logs.json` | Buildkite logs: job output by pipeline, step and build |
| `buildkite-traces.json` | Buildkite traces: durations, phases and a list of jobs |
| `buildkite-infra.json` | Buildkite infrastructure: agent-stack-k8s controller health |

They pick the stack's data sources automatically. To change them, edit
`observability/dashboards/make_dashboards.py`, regenerate, and re-import with
overwrite; see `observability/README.md`.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Jobs stay "waiting for agent" | No controller is serving the step's queue | Check the step's `agents.queue` is `kube` and the controller log shows `queue=kube`. |
| Job pods fail to start, `serviceaccount "buildkite-agent" not found` | Service account missing | `kubectl -n buildkite create serviceaccount buildkite-agent` |
| Logs reach Grafana but traces don't | Tracing set with the v3 variable | Make sure `BUILDKITE_OPENTELEMETRY_TRACING=true` is on the agent container (`helm/values.yaml`). |
| Nothing from job pods reaches Grafana | `buildkite-otel-env` missing or wrong | `kubectl -n buildkite get cm buildkite-otel-env -o yaml` should point at `grafana-cloud-otel-receiver.monitoring.svc.cluster.local:4317`. |
| Alloy pods not starting | `grafana-cloud-remote-cfg` Secret missing | Create it (step 2), then `kubectl -n monitoring rollout restart ds/grafana-cloud-alloy`. |
| Cluster metrics or Kubernetes events missing | Fleet Management pipelines not set up | Finish the Kubernetes Monitoring setup in Grafana Cloud (step 2.4). |
| Controller metrics missing | Annotations not on the controller pod | `helm/values.yaml` sets them; re-run the step 3 upgrade. |
| Alloy logs `failed to get informer` for podmonitors, servicemonitors, probes | No Prometheus Operator CRDs in this cluster | Harmless; see "Known noise" in `observability/README.md`. |
| A build doesn't start after a push | GitHub webhook delayed or not delivered | Check githubstatus.com, or start the build from **New Build** in Buildkite. |

## Credentials

No credential is stored in this repository:

| Credential | Lives in |
|------------|----------|
| Buildkite cluster agent token | Helm release `agent-stack-k8s` (Secret `agent-stack-k8s-secrets` in `buildkite`) |
| Grafana Cloud instance ID and token | Secret `grafana-cloud-remote-cfg` in `monitoring` |

To rotate the Grafana Cloud token, recreate the Secret with
`--dry-run=client -o yaml | kubectl apply -f -` added to the command in step 2,
run `kubectl -n monitoring rollout restart ds/grafana-cloud-alloy`, then delete
the old token in Grafana Cloud.
