# Observability stack

Grafana, Tempo, Loki and Prometheus in the `observability` namespace of the
rancher-desktop cluster. `otel-gateway` (namespace `otel-lab`) stays the single
OTLP entry point and forwards each signal here.

```
job pods / agent ──OTLP──▶ otel-gateway (otel-lab)
                            ├─ traces  ─▶ Tempo       ─┐
                            ├─ logs    ─▶ Loki         ├─▶ Grafana
                            └─ metrics ─▶ Prometheus  ─┘
```

| Component  | Chart                                  | Values                    | In-cluster address                                  |
|------------|----------------------------------------|---------------------------|-----------------------------------------------------|
| Tempo      | `grafana-community/tempo` 3.2.0        | `tempo-values.yaml`       | `tempo.observability:3200` (query), `:4317` (OTLP)  |
| Loki       | `grafana/loki` 7.3.0                   | `loki-values.yaml`        | `loki.observability:3100`, OTLP at `/otlp`          |
| Prometheus | `prometheus-community/prometheus` 29.37.0 | `prometheus-values.yaml` | `prometheus-server.observability:80`, remote write at `/api/v1/write` |
| Grafana    | `grafana-community/grafana` 14.0.1     | `grafana-values.yaml`     | `http://grafana.127.0.0.1.nip.io`                   |

Each component keeps 7 days of data on a `local-path` volume (5Gi; 1Gi for
Grafana). Together they request about 1 GiB of memory, with limits of 3.5 GiB.

## Install

```sh
helm repo add grafana https://grafana.github.io/helm-charts
helm repo add grafana-community https://grafana-community.github.io/helm-charts
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

kubectl create namespace observability

helm upgrade --install prometheus prometheus-community/prometheus --version 29.37.0 \
  -n observability -f observability/prometheus-values.yaml
helm upgrade --install tempo grafana-community/tempo --version 3.2.0 \
  -n observability -f observability/tempo-values.yaml
helm upgrade --install loki grafana/loki --version 7.3.0 \
  -n observability -f observability/loki-values.yaml
helm upgrade --install grafana grafana-community/grafana --version 14.0.1 \
  -n observability -f observability/grafana-values.yaml
```

Prometheus goes first because Tempo's metrics generator writes to it.

Then point the gateway at the backends:

```sh
helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts
helm upgrade otel-gateway open-telemetry/opentelemetry-collector --version 0.173.1 \
  -n otel-lab -f observability/otel-gateway-values.yaml
```

`otel-gateway-values.yaml` holds the gateway's whole config, so don't use
`--reuse-values` with it. Besides OTLP, the gateway scrapes the agent-stack-k8s
controller (port 9216) and itself (port 8888). In Prometheus those series carry
`job="buildkite/agent-stack-k8s"` and `job="otel-lab/otelcol-contrib"`.

## Sign in to Grafana

Open http://grafana.127.0.0.1.nip.io. Rancher Desktop forwards 127.0.0.1:80 to
Traefik, and nip.io is public DNS that answers with the address in the name, so
this works in any browser without editing `/etc/hosts`.

Don't use `grafana.localhost`: it resolves to `::1` first, and on this Mac
Docker Desktop holds `[::1]:80`, so the request never reaches Traefik.

Without internet DNS, port-forward instead. Port 3000 is already taken on this
Mac, so use 3300 and bind IPv4 only:

```sh
kubectl -n observability port-forward --address 127.0.0.1 svc/grafana 3300:80
```

Then open http://127.0.0.1:3300.

The user is `admin`. The chart generates the password:

```sh
kubectl -n observability get secret grafana -o jsonpath='{.data.admin-password}' | base64 -d; echo
```

## How the pieces link up

- **Span to logs:** the Tempo data source queries Loki for
  `{service_name="buildkite-agent"} | trace_id="<trace>"`. Loki stores the OTLP
  `trace_id` as structured metadata.
- **Log to trace:** the Loki data source turns the `trace_id` metadata into a
  link to Tempo.
- **Span metrics:** Tempo writes `traces_spanmetrics_*` histograms to Prometheus,
  labelled by span name, status, `buildkite_pipeline`, `buildkite_job_label`,
  `buildkite_queue` and `buildkite_branch`. They undercount CI jobs: each new
  series reaches Prometheus starting at 1, so `increase()` never sees its
  first job, and a step that is idle for 15 minutes starts a new series. Use
  them for trends and alerts. The dashboard gets exact counts and averages from
  TraceQL metrics instead.

Loki indexes only `service_name`, `k8s_cluster_name` and `k8s_namespace_name`.
The Buildkite fields arrive as log attributes, so Loki keeps them, and
`trace_id`, as structured metadata. Filter them after the stream selector:

```logql
{service_name="buildkite-agent"} | buildkite_pipeline_slug="ao-tests" | buildkite_build_number="86"
```

Under agent-stack-k8s, each job produces two traces, one for checkout and one
for the command container. Both carry the same `buildkite.job_id`.

## Buildkite CI dashboard

**Dashboards → Buildkite → Buildkite CI**
(http://grafana.127.0.0.1.nip.io/d/buildkite-ci). Pick pipelines at the top;
the log search box filters the job logs panel.

| Row | Shows | Source |
|-----|-------|--------|
| Overview | Jobs run, failed jobs, average command and checkout time, jobs in flight | Tempo (TraceQL metrics), Prometheus |
| Where job time goes | Average time per phase, p95 of checkout / git fetch / command, runs, time and failures per step | Tempo |
| Agent CLI overhead | Time spent in `pipeline upload`, `annotate`, `meta-data` and artifact calls | Tempo |
| Failures | Recent failed traces, and error lines from job logs | Tempo, Loki |
| Job logs | Every job log line; open a line for its build, job and trace link | Loki |
| agent-stack-k8s controller | Jobs in flight against the limit, time to create the Kubernetes Job, stalled or failed jobs | Prometheus |
| Telemetry pipeline | What otel-gateway receives, and any export failures | Prometheus |

Tempo reports quantiles in power-of-two buckets, so the p95 values are
approximate. Counts and averages are exact. TraceQL metrics queries are allowed
up to 7 days, the retention.

The dashboard is generated by `dashboards/make_dashboard.py` and loaded by
Grafana's sidecar from a ConfigMap labelled `grafana_dashboard`. It's
provisioned, so edits in the UI can't be saved over it; change the script
instead, then:

```sh
python3 observability/dashboards/make_dashboard.py observability/dashboards/buildkite-ci.json
kubectl -n observability create configmap grafana-dashboard-buildkite-ci \
  --from-file=observability/dashboards/buildkite-ci.json --dry-run=client -o yaml \
  | kubectl label --local -f - grafana_dashboard=1 -o yaml \
  | kubectl apply -f -
```

Grafana picks up the change within about 30 seconds, without a restart.
