# Observability

Buildkite telemetry from the rancher-desktop cluster goes to Grafana Cloud.
`otel-gateway` (namespace `otel-lab`) is the single OTLP entry point: job pods
and the agent-stack-k8s controller send to it, and it forwards everything to
the Grafana Cloud stack.

```
job pods (agent v4) ──OTLP──▶ otel-gateway (otel-lab) ──OTLP/HTTP──▶ Grafana Cloud
agent-stack-k8s :9216 ◀─scrape─┘      │                              ├─ traces  (Tempo)
otel-gateway    :8888 ◀─scrape────────┘                              ├─ logs    (Loki)
                                                                      └─ metrics (Mimir)
```

| What | Where |
|------|-------|
| Gateway | Helm release `otel-gateway`, chart `open-telemetry/opentelemetry-collector` 0.173.1, collector-contrib 0.160.0, values in `otel-gateway-values.yaml` |
| Grafana Cloud stack | Instance `1884458`, region `prod-us-east-2`, endpoint `https://otlp-gateway-prod-us-east-2.grafana.net/otlp` |
| Credentials | Secret `grafana-cloud-otlp` in `otel-lab`, created by hand, never committed |
| Dashboards | `dashboards/`, imported into Grafana Cloud |

How the job pods are configured to send to the gateway is in `helm/README.md`.

## Install or update the gateway

Create the credentials Secret first, or the gateway pod won't start:

```sh
read -rs GRAFANA_CLOUD_API_TOKEN   # paste the access policy token; it isn't echoed
kubectl -n otel-lab create secret generic grafana-cloud-otlp \
  --from-literal=GRAFANA_CLOUD_INSTANCE_ID=1884458 \
  --from-literal=GRAFANA_CLOUD_API_TOKEN="$GRAFANA_CLOUD_API_TOKEN"
unset GRAFANA_CLOUD_API_TOKEN
```

The token needs an access policy with `metrics:write`, `logs:write` and
`traces:write`. Then install or upgrade the gateway:

```sh
helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts
helm upgrade --install otel-gateway open-telemetry/opentelemetry-collector --version 0.173.1 \
  -n otel-lab -f observability/otel-gateway-values.yaml
```

`otel-gateway-values.yaml` holds the gateway's whole config, so don't use
`--reuse-values` with it.

**Rotating the token:** create the Secret again with the new token, adding
`--dry-run=client -o yaml | kubectl apply -f -` to the `create` command, then
run `kubectl -n otel-lab rollout restart deploy/otel-gateway`. Delete the old
token in Grafana Cloud afterwards.

**The endpoint comes from the stack's region, not the token's.** This token was
issued in `prod-us-east-0`, but the stack is in `prod-us-east-2`. The
`prod-us-east-0` gateway accepts the credentials and then returns 502 for every
request.

## What the gateway sends

- **Traces:** one span per job phase (`checkout`, `git.*`, hooks, `command`)
  and per agent CLI call (`pipeline-upload`, `annotate`, `meta-data-*`). Each
  carries resource attributes such as `buildkite.pipeline`,
  `buildkite.build_number`, `buildkite.job_label`, `buildkite.queue` and
  `buildkite.job_id`, plus Kubernetes attributes added by the gateway.
- **Logs:** every line of job output, with `trace_id` and the Buildkite fields.
- **Metrics:** the agent-stack-k8s controller (`job="buildkite/agent-stack-k8s"`,
  for example `buildkite_limiter_max_in_flight`) and the gateway's own counters
  (`job="otel-lab/otelcol-contrib"`, `otelcol_*`).

Under agent-stack-k8s, each job produces two traces, one for the checkout
container and one for the command container. Both carry the same
`buildkite.job_id`.

In Loki, the Buildkite fields are structured metadata, not labels, so filter
them after the stream selector:

```logql
{service_name="buildkite-agent"} | buildkite_pipeline_slug="ao-tests" | buildkite_build_number="88"
```

## Dashboards

Four dashboards, linked to each other from the top-right **Buildkite
dashboards** menu:

| Dashboard | Use it to | Data |
|-----------|-----------|------|
| **Buildkite CI** (`buildkite-ci.json`) | See jobs, failures and where job time goes at a glance | Traces, logs, metrics |
| **Buildkite logs** (`buildkite-logs.json`) | Find job output: volume by pipeline and step, error lines, lines per job, full-text search | Logs |
| **Buildkite traces** (`buildkite-traces.json`) | Dig into durations: jobs and failures over time, p95 per step, phase and git timings, sortable job list | Traces |
| **Buildkite infrastructure** (`buildkite-infra.json`) | Check agent-stack-k8s and otel-gateway health, including what the gateway sends | Metrics |

The variables at the top pick the stack's `grafanacloud-<stack>-traces`,
`-logs` and `-prom` data sources, the pipelines, and on the logs dashboard a
build number regex, part of a step label and a search string.

Counts and averages from spans use TraceQL metrics, so they're exact. Tempo
reports quantiles in power-of-two buckets, so p95 values are approximate.

### Changing the dashboards

`dashboards/make_dashboards.py` generates all four JSON files. Change the
script rather than the JSON, then regenerate:

```sh
python3 observability/dashboards/make_dashboards.py observability/dashboards
```

Import each changed file in Grafana Cloud under **Dashboards → New → Import**.
The `uid` stays the same, so choose to overwrite the existing dashboard.
