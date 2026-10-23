# Observability

The cluster is monitored with Grafana Cloud's supported Kubernetes setup: the
`grafana/k8s-monitoring` Helm chart, which runs Grafana Alloy collectors in the
`monitoring` namespace. Buildkite job pods send their traces and logs to Alloy
over OTLP, and Alloy forwards everything to the Grafana Cloud stack.

```
Buildkite job pods ──OTLP──▶ Alloy (monitoring) ──▶ Grafana Cloud stack 1884458
agent-stack-k8s    ◀─scrape─┘   │                     (prod-us-east-2)
cluster, nodes, pods ◀─collect──┘   ▲
                                    └── pipelines from Fleet Management
```

| What | Where |
|------|-------|
| Helm release | `grafana-cloud` in `monitoring`, chart `grafana/k8s-monitoring` 4.5.3, values in `k8s-monitoring-values.yaml` |
| Collector | DaemonSet `grafana-cloud-alloy` (one Alloy per node) |
| OTLP endpoint for applications | `grafana-cloud-otel-receiver.monitoring.svc.cluster.local:4317` (gRPC), `:4318` (HTTP) |
| Credentials | Secret `grafana-cloud-remote-cfg` in `monitoring`, created by hand, never committed |
| Dashboards | `dashboards/`, imported into Grafana Cloud |

## Where each pipeline is defined

Alloy runs two kinds of configuration side by side:

| Defined in | Pipeline | Collects |
|------------|----------|----------|
| Grafana Cloud **Fleet Management** (set up in the Kubernetes Monitoring UI) | `k8s_monitoring_k8s_monitoring_my_cluster` | Cluster and node metrics (kube-state-metrics, node-exporter, kubelet, cAdvisor), Kubernetes events, changes to Kubernetes objects, and pods annotated for scraping. It doesn't collect pod container logs. |
| Fleet Management | `ihub_kubernetes_survey_k8s_monitoring` | Beyla workload discovery for Instrumentation Hub |
| This repo, `k8s-monitoring-values.yaml` | `applicationObservability` feature + `grafanaCloudOtlp` destination | OTLP from applications: the Buildkite job pods' traces and logs |

Fleet Management matches its cluster pipeline to collectors by cluster name
(`cluster="my-cluster"`), so `cluster.name` in `k8s-monitoring-values.yaml` must
be the name chosen in the Grafana Cloud onboarding. If they differ, Alloy runs
but collects no cluster metrics or events.

Fleet Management doesn't offer an OTLP pipeline, which is why that part lives
in the values file. Alloy polls Fleet Management every 30 seconds, so pipeline
changes made in Grafana Cloud apply without a redeploy.

## Install or update

Create the namespace and the credentials Secret first. The token needs Fleet
Management access and write scopes for metrics, logs and traces; the Grafana
Cloud Kubernetes onboarding creates one with those.

```sh
kubectl create namespace monitoring

read -rs GRAFANA_CLOUD_TOKEN   # paste the token; it isn't echoed
kubectl -n monitoring create secret generic grafana-cloud-remote-cfg \
  --from-literal=username=1884458 \
  --from-literal=password="$GRAFANA_CLOUD_TOKEN"
unset GRAFANA_CLOUD_TOKEN

helm repo add grafana https://grafana.github.io/helm-charts
helm repo update grafana
helm upgrade --install grafana-cloud grafana/k8s-monitoring --version 4.5.3 \
  --namespace monitoring \
  --values observability/k8s-monitoring-values.yaml
```

Then point the Buildkite job pods at Alloy (already the case in this repo):

```sh
kubectl apply -f helm/otel-env.yaml
```

**Check:** all pods in `monitoring` are `Running`, and Alloy logs
`successfully loaded remote configuration` and `Starting GRPC server … :4317`:

```sh
kubectl -n monitoring get pods
kubectl -n monitoring logs ds/grafana-cloud-alloy -c alloy \
  | grep -E 'successfully loaded remote configuration|Starting (GRPC|HTTP) server'
```

### Credentials, without the token in Helm

Grafana Cloud's onboarding snippet passes the token with `--set-string …password=`,
which stores it in the Helm release. This setup reads it from the
`grafana-cloud-remote-cfg` Secret instead:

- **Fleet Management connection:** `remoteConfig.secret.create: false` and
  `secret.name`. `remoteConfig.tls: null` stops the chart from also expecting
  TLS keys in that Secret.
- **OTLP destination:** the chart already passes the token to Alloy as the
  environment variable `GCLOUD_RW_API_KEY`, so the destination uses
  `passwordFrom: sys.env("GCLOUD_RW_API_KEY")` with `secret.embed: true`.

**Rotating the token:** recreate the Secret with the new token (add
`--dry-run=client -o yaml | kubectl apply -f -` to the `create` command), then
`kubectl -n monitoring rollout restart ds/grafana-cloud-alloy`.

## What Buildkite sends

- **Traces:** one span per job phase (`checkout`, `git.*`, hooks, `command`)
  and per agent CLI call. Resource attributes include `buildkite.pipeline`,
  `buildkite.build_number`, `buildkite.job_label` and `buildkite.job_id`;
  Alloy adds Kubernetes attributes (pod, Job, namespace).
- **Logs:** every line of job output, with `trace_id` and the Buildkite fields.
- **Metrics:** the agent-stack-k8s controller, scraped by Alloy because its pod
  carries `k8s.grafana.com/scrape: "true"` annotations (set in
  `helm/values.yaml`), with `job="buildkite/agent-stack-k8s"`.

Each job produces two traces, one from the checkout container and one from the
command container, sharing `buildkite.job_id`. In Loki the Buildkite fields are
structured metadata, so filter them after the selector:

```logql
{service_name="buildkite-agent"} | buildkite_pipeline_slug="ao-tests" | buildkite_build_number="100"
```

## Dashboards

Four dashboards, linked from the top-right **Buildkite dashboards** menu. Grafana
Cloud's Kubernetes Monitoring app covers the cluster itself.

| Dashboard | Use it to | Data |
|-----------|-----------|------|
| **Buildkite CI** (`buildkite-ci.json`) | See builds, failures and queue wait for every pipeline, then where job time goes inside `kube` jobs | Traces (Buildkite's OpenTelemetry integration and the agent), logs, metrics |
| **Buildkite logs** (`buildkite-logs.json`) | Find job output: a recent-builds table to pick a build, volume by pipeline and step, error lines, lines per job, full-text search | Logs, traces (integration, for the builds table) |
| **Buildkite traces** (`buildkite-traces.json`) | Dig into durations: builds and failed builds per pipeline, build duration and queue wait trends, sortable builds and queue-wait tables; then jobs on `kube`, phase and git timings, sortable job list | Traces (integration and agent) |
| **Buildkite infrastructure** (`buildkite-infra.json`) | Check the agent-stack-k8s controller (jobs in flight, time to create Kubernetes Jobs, failures) and queue wait and jobs started per queue | Metrics, traces (integration) |

Each dashboard also has a section fed by Buildkite's OpenTelemetry integration
(`service.name` = `buildkite`): builds, steps and jobs as Buildkite sees them,
including `buildkite.job.wait_time_ms`, for every pipeline and queue. That's the
top section of **Buildkite CI** and **Buildkite traces**, the recent-builds table
on **Buildkite logs**, and the queue-wait row on **Buildkite infrastructure**.
These stay empty until the integration is on. To send it to this stack, add an
OpenTelemetry notification service under **Organization settings → Notification
Services** with endpoint `https://otlp-gateway-prod-us-east-2.grafana.net/otlp`
and header `Authorization: Basic <base64 of instance-id:token>`, using a token
with `traces:write`. Everything else uses the agent's spans and logs from jobs
on `kube`. The pipeline lists come from the agent's `buildkite.pipeline` tag, so
they show pipelines that ran on `kube` in the dashboard's time range; **All** still
includes every pipeline in the integration panels.

The variables at the top pick the stack's `grafanacloud-<stack>-traces`,
`-logs` and `-prom` data sources and the pipelines. Counts and averages from
spans use TraceQL metrics, so they're exact; p95 values are approximate.

### Changing the dashboards

`dashboards/make_dashboards.py` generates all four JSON files; `make dashboards`
fails if the committed JSON is out of date. Change the script, then:

```sh
python3 observability/dashboards/make_dashboards.py observability/dashboards
```

Import each changed file in Grafana Cloud under **Dashboards → New → Import**
and choose to overwrite.

## Known noise

The Fleet Management cluster pipeline includes Prometheus Operator components
(`podmonitors`, `servicemonitors`, `probes`). This cluster has no Prometheus
Operator CRDs, so Alloy logs `failed to get informer` warnings and
`error running crd manager` for them. Nothing is lost. To silence them, turn off
Prometheus Operator objects in the Kubernetes Monitoring configuration, or
install the CRDs.
