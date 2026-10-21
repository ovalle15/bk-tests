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
        └──────── scraped for metrics ──────────────── otel-gateway (namespace otel-lab)
                                                            │ OTLP over HTTPS
                                                            ▼
                                                    Grafana Cloud: Tempo, Loki, Mimir
                                                            │
                                                            ▼
                                                    Buildkite dashboards
```

This README covers the whole setup in order. The details for each part live
next to its config:

| Part | Config | Details |
|------|--------|---------|
| Job runner | `helm/values.yaml`, `helm/otel-env.yaml` | `helm/README.md` |
| Telemetry gateway and Grafana Cloud | `observability/otel-gateway-values.yaml` | `observability/README.md` |
| Dashboards | `observability/dashboards/` | `observability/README.md` |
| Queue routing | `.buildkite/queue-routing.json` | `.buildkite/README.queues.md` |

## What you need

- **A Kubernetes cluster** and `kubectl` pointed at it. This repo uses Rancher
  Desktop (context `rancher-desktop`).
- **Helm 3.**
- **A Buildkite organization** with a cluster and permission to create queues
  and agent tokens.
- **A Grafana Cloud stack** and permission to create access policies.

Versions this setup was built and tested with:

| Component | Version |
|-----------|---------|
| agent-stack-k8s chart | 0.50.0 |
| Buildkite agent in job pods | 4.0.3 (the controller's default image) |
| opentelemetry-collector chart | 0.173.1 |
| otel/opentelemetry-collector-contrib | 0.160.0 |

## 1. Buildkite: create the queue and an agent token

1. In Buildkite, open your cluster (here `test-cluster`) and create a
   **self-hosted** queue with the key `kube`.
2. On the cluster's **Agent Tokens** page, create a token for the controller.
   Keep it out of the repo; you'll pass it to Helm in step 2.
3. Point pipeline steps at the queue. `command` is whatever the step runs; for
   example, this repo's queue-routing check:

   ```yaml
   steps:
     - label: ":cat: Validate queue routing"
       command: python3 scripts/queue_router.py validate
       agents:
         queue: kube
   ```

## 2. Kubernetes: install the agent-stack-k8s controller

The controller polls the `kube` queue and creates a Kubernetes Job for each
Buildkite job, so no agent pods run between builds.

```sh
kubectl create namespace buildkite

# Job pods run as this service account (set in helm/values.yaml).
kubectl -n buildkite create serviceaccount buildkite-agent

# OTLP endpoint for job pods. Apply it before the controller creates any jobs.
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

`helm/values.yaml` does more than set the queue. It patches every job pod so
the agent sends OpenTelemetry data:

| Setting | Effect |
|---------|--------|
| `BUILDKITE_OPENTELEMETRY_TRACING=true` | The agent records a span for each job phase. |
| `BUILDKITE_JOB_LOGS_OTLP=true` | Each line of job output is sent as a log record. |
| `BUILDKITE_TELEMETRY_SERVICE_NAME=buildkite-agent` | Sets `service.name` on everything the agent sends. |
| `envFrom: buildkite-otel-env` | Sends to `otel-gateway.otel-lab.svc.cluster.local:4317`. |

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

## 3. Grafana Cloud: create a token for sending data

1. In the Grafana Cloud portal, open your stack and find the **OpenTelemetry**
   tile. Click **Configure** and note the **Instance ID** and the **OTLP
   endpoint**. Use the endpoint shown there, which is set by the stack's region
   (here `prod-us-east-2`), not by where the token is issued.
2. Create an access policy with the scopes `metrics:write`, `logs:write` and
   `traces:write`, and a token for it.
3. Store both in the cluster:

   ```sh
   kubectl create namespace otel-lab

   read -rs GRAFANA_CLOUD_API_TOKEN   # paste the token; it isn't echoed
   kubectl -n otel-lab create secret generic grafana-cloud-otlp \
     --from-literal=GRAFANA_CLOUD_INSTANCE_ID=<instance id> \
     --from-literal=GRAFANA_CLOUD_API_TOKEN="$GRAFANA_CLOUD_API_TOKEN"
   unset GRAFANA_CLOUD_API_TOKEN
   ```

You can check the credentials before installing anything. An empty request
should return 200 (or 204 for logs), not 401 or 502:

```sh
curl -s -o /dev/null -w '%{http_code}\n' -u "<instance id>:<token>" \
  -H 'content-type: application/json' -d '{}' \
  https://otlp-gateway-prod-us-east-2.grafana.net/otlp/v1/traces
```

## 4. Kubernetes: install otel-gateway

The gateway is an OpenTelemetry Collector. It receives OTLP from job pods,
scrapes the controller's metrics, adds Kubernetes details (pod, Job, namespace)
and forwards everything to Grafana Cloud.

If your stack isn't instance `1884458` in `prod-us-east-2`, first change the
`otlphttp/grafana_cloud` endpoint in `observability/otel-gateway-values.yaml`
to the one from step 3.

```sh
helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts
helm repo update open-telemetry
helm upgrade --install otel-gateway open-telemetry/opentelemetry-collector \
  --namespace otel-lab \
  --version 0.173.1 \
  --values observability/otel-gateway-values.yaml
```

The values file holds the gateway's whole config, so don't add
`--reuse-values`. The gateway won't start until the `grafana-cloud-otlp` Secret
from step 3 exists.

**Check:** the pod is `Running` and its log ends with `Everything is ready`,
with no `error` lines:

```sh
kubectl -n otel-lab get pods
kubectl -n otel-lab logs deploy/otel-gateway | grep -E 'Everything is ready|error'
```

## 5. Run a build and find it in Grafana

1. Run any pipeline whose steps use `queue: kube`. While it runs,
   `kubectl -n buildkite get pods` shows a `buildkite-<job id>-…` pod per job.
2. In Grafana Cloud, open **Explore** and look for the build. Metrics show up
   within a minute even without a build, because the gateway scrapes every 30
   seconds.

| Signal | Data source | Query |
|--------|-------------|-------|
| Metrics | `grafanacloud-<stack>-prom` | `buildkite_limiter_max_in_flight` (returns your `max-in-flight`, 10) |
| Traces | `grafanacloud-<stack>-traces` | `{ resource.buildkite.pipeline = "<pipeline slug>" }` |
| Logs | `grafanacloud-<stack>-logs` | `{service_name="buildkite-agent"} \| buildkite_pipeline_slug="<pipeline slug>"` |

Each job produces two traces, one from the checkout container and one from the
command container. Both carry the same `buildkite.job_id`.

## 6. Grafana Cloud: import the dashboards

Import each file from `observability/dashboards/` under **Dashboards → New →
Import**:

| File | Dashboard |
|------|-----------|
| `buildkite-ci.json` | Buildkite CI: jobs, failures and where job time goes |
| `buildkite-logs.json` | Buildkite logs: job output by pipeline, step and build |
| `buildkite-traces.json` | Buildkite traces: durations, phases and a list of jobs |
| `buildkite-infra.json` | Buildkite infrastructure: controller and gateway health |

They pick the stack's data sources automatically. To change them, edit
`observability/dashboards/make_dashboards.py`, regenerate, and re-import with
overwrite; see `observability/README.md`.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Jobs stay "waiting for agent" | No controller is serving the step's queue | Check the step's `agents.queue` is `kube` and the controller log shows `queue=kube`. |
| Job pods fail to start, `serviceaccount "buildkite-agent" not found` | Service account missing | `kubectl -n buildkite create serviceaccount buildkite-agent` |
| Logs reach Grafana but traces don't | Tracing set with the v3 variable | Make sure `BUILDKITE_OPENTELEMETRY_TRACING=true` is on the agent container (`helm/values.yaml`). |
| Nothing from job pods reaches Grafana | `buildkite-otel-env` missing or wrong | `kubectl -n buildkite get cm buildkite-otel-env -o yaml` should point at `otel-gateway.otel-lab.svc.cluster.local:4317`. |
| Gateway pod stuck in `CreateContainerConfigError` | `grafana-cloud-otlp` Secret missing | Create it (step 3), then `kubectl -n otel-lab rollout restart deploy/otel-gateway`. |
| Gateway log shows 401 | Wrong instance ID or token | Recreate the Secret, then restart the gateway. |
| Gateway log shows 502 from Grafana Cloud | Endpoint is in the wrong region | Use the endpoint from the stack's OpenTelemetry page. |
| Explore shows no data | Different stack, data source or time range | Check the stack's instance ID matches the Secret and widen the time range. |

## Credentials

No credential is stored in this repository:

| Credential | Lives in |
|------------|----------|
| Buildkite cluster agent token | Helm release `agent-stack-k8s` (Secret `agent-stack-k8s-secrets` in `buildkite`) |
| Grafana Cloud instance ID and token | Secret `grafana-cloud-otlp` in `otel-lab` |

To rotate the Grafana Cloud token, recreate the Secret with
`--dry-run=client -o yaml | kubectl apply -f -` added to the command in step 3,
run `kubectl -n otel-lab rollout restart deploy/otel-gateway`, then delete the
old token in Grafana Cloud.
