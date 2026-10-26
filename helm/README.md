# Agent Stack for Kubernetes

Kubernetes jobs run on the `kube` queue, which the `agent-stack-k8s` controller
(chart 0.50.0, namespace `buildkite`) serves. The controller creates one
Kubernetes Job per Buildkite job, so there is no long-running agent pod.

Job pods send spans and job logs over OTLP to the k8s-monitoring Alloy
collector (`grafana-cloud-otel-receiver` in `monitoring`; the endpoint is in
`otel-env.yaml`). The `k8s.grafana.com/*` annotations in `values.yaml` let Alloy
scrape the controller's own metrics. See `observability/README.md`.

Job pods run agent v4, which turns tracing on with
`BUILDKITE_OPENTELEMETRY_TRACING`. The controller's `agent-config.tracing-backend`
option sets the v3 variable, which v4 ignores, so `values.yaml` sets the v4
variables directly on the agent container. The checkout and command containers
each run their own bootstrap, so every job produces two traces, one per
container, with the same `buildkite.job_id`.

## Apply

```sh
kubectl apply -f helm/otel-env.yaml
helm upgrade agent-stack-k8s oci://ghcr.io/buildkite/helm/agent-stack-k8s \
  --namespace buildkite \
  --version 0.50.0 \
  --reuse-values \
  --values helm/values.yaml
```

`--reuse-values` keeps the `agentToken` already set on the release, so the token
never needs to be in this repository.

## Leftovers from the standalone agent

Before agent-stack-k8s, a long-running `buildkite-agent` Deployment served the
Buildkite `otel-lab` queue. It's scaled to zero and nothing routes to that queue
any more. It, its `buildkite-agent-metrics` Service, and the `default/agent`
Deployment (also at zero) can be deleted:

```sh
kubectl -n buildkite delete deployment buildkite-agent
kubectl -n buildkite delete service buildkite-agent-metrics
kubectl -n default delete deployment agent
```

Keep the `buildkite-agent` service account and the `buildkite-agent-token`
Secret: job pods use the service account, and the Secret may still be in use.

## Choosing the job image

Unless a step names an image, it runs in the controller's default
`buildkite/agent` image. To use another image, set it with the `kubernetes`
plugin:

```yaml
plugins:
  - kubernetes:
      podSpec:
        containers:
          - image: python:3.12
```
