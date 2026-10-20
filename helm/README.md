# Agent Stack for Kubernetes

Kubernetes jobs run on the `kube` queue, which the `agent-stack-k8s` controller
(chart 0.50.0, namespace `buildkite`) serves. The controller creates one
Kubernetes Job per Buildkite job, so there is no long-running agent pod.

The tracing settings in `values.yaml` and `otel-env.yaml` come from the old
standalone `buildkite-agent` Deployment, which served the `otel-lab` queue.
Spans and job logs go to `otel-gateway` in the `otel-lab` namespace.

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

## Retire the standalone agent

Once a build on `kube` passes:

```sh
kubectl scale deployment buildkite-agent --namespace buildkite --replicas=0
```

Delete the Deployment after the `otel-lab` queue has stayed empty for a while.
The `default/agent` Deployment is already scaled to zero and can go too.

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
