# Buildkite Stacks API Docker lab

This lab demonstrates how to implement the controller side of a custom
Buildkite stack. It discovers jobs from a self-hosted queue, reserves a job,
issues a short-lived Job Acquisition Token (JAT), and starts a one-shot
Buildkite agent in Docker.

The existing `scripts/webhook-acquire-agent.py` exercise is related, but it is
not a Stacks API implementation. That script receives `job.scheduled` webhook
events and gives the long-lived cluster agent token directly to the worker.
In this lab, the controller polls the Stacks API and retains the long-lived
token; the worker receives only a job-specific JAT.

## Architecture

```text
Buildkite queue
      |
      | list scheduled jobs
      v
custom stack controller
      |
      | reserve job and request JAT
      v
one-shot Docker agent
      |
      | --acquire-job <job UUID>
      v
Buildkite job runs and agent exits
```

## API endpoints covered

| Operation | Method and path | Typical use |
| --- | --- | --- |
| Register stack | `POST /v3/stacks/register` | Announce or update a controller |
| Deregister stack | `POST /v3/stacks/:key/deregister` | Remove a stopped controller |
| List scheduled jobs | `GET /v3/stacks/:key/scheduled-jobs` | Discover queue demand |
| Get job | `GET /v3/stacks/:key/jobs/:id` | Read the command and environment |
| Reserve jobs | `PUT /v3/stacks/:key/scheduled-jobs/batch-reserve` | Prevent duplicate execution |
| Issue JATs | `POST /v3/stacks/:key/job-acquisition-tokens` | Give a worker job-scoped credentials |
| Get job states | `POST /v3/stacks/:key/jobs/get-states` | Detect cancellation while provisioning |
| Finish job | `POST /v3/stacks/:key/jobs/:id/finish` | Report an infrastructure failure |
| Create notifications | `POST /v3/stacks/:key/notifications` | Show provisioning progress in Buildkite |

The Stacks API is hosted at `https://agent.buildkite.com`. It authenticates
with a cluster agent token in the form `bkct_...`, using this header:

```text
Authorization: Token <cluster-agent-token>
```

This is not the Buildkite REST/GraphQL API and does not use a bearer API token
or an organization ID. The token determines the organization and cluster.

## Safety and isolation

Use a disposable pipeline and the dedicated `webhook-acquire` queue for this
lab. Before starting:

1. Stop `scripts/webhook-acquire-agent.py` with `Ctrl-C`.
2. Confirm no persistent agent listens on `webhook-acquire`.
3. Do not run the Kubernetes Agent Stack against this queue.
4. Use `finish` only on a disposable job because it deliberately completes
   that job.

If another controller or agent listens on the queue, it may claim the job
before the API exercises can inspect or reserve it.

## Prerequisites

- A Buildkite cluster containing the self-hosted queue `webhook-acquire`.
- A cluster agent token from that cluster's **Agent Tokens** page.
- `curl`, `jq`, and Docker.
- The local agent image built from `.buildkite/docker-agent/Dockerfile`.
- A default agent or another mechanism capable of uploading pipeline YAML, if
  the lab pipeline uses a dynamic upload step.

Build the Docker agent image:

```bash
docker build \
  --tag ao-buildkite-acquire-agent:local \
  .buildkite/docker-agent
```

## Create a disposable pipeline job

Create a separate Buildkite pipeline for the lab. Configure a command step
like this:

```yaml
steps:
  - label: ":docker: Stacks API lab"
    key: "stacks-api-lab"
    command: |
      echo "Acquired by the custom Docker stack"
      echo "Job: $$BUILDKITE_JOB_ID"
      sleep 20
    agents:
      queue: "webhook-acquire"
```

The doubled dollar sign is necessary when this YAML is uploaded from another
Buildkite job. If the YAML is entered directly in the Buildkite pipeline
editor, a single `$BUILDKITE_JOB_ID` also works.

Do not start a build yet.

## Prepare the shell

Run the lab from the repository root:

```bash
cd /Users/andrea_ovalle/Development/bk_playground/ao-bk-playground
source ~/.bash_profile

export STACK_KEY="docker-stack-lab"
export QUEUE_KEY="webhook-acquire"
```

If `BUILDKITE_AGENT_TOKEN` contains a 1Password reference rather than the
resolved value, resolve it for the current shell without printing it:

```bash
if [[ "$BUILDKITE_AGENT_TOKEN" == op://* ]]; then
  export BUILDKITE_AGENT_TOKEN="$(op read "$BUILDKITE_AGENT_TOKEN")"
fi
```

Validate only its presence and prefix:

```bash
case "$BUILDKITE_AGENT_TOKEN" in
  bkct_*) echo "Cluster agent token is ready" ;;
  *) echo "BUILDKITE_AGENT_TOKEN is missing or is not a cluster token" >&2 ;;
esac
```

Never enable shell tracing with `set -x` while these credentials are present.

## 1. Register the custom stack

Every stack key must be registered before using the remaining endpoints.
Registration is idempotent: it creates a new stack or updates an existing
stack with the same key.

```bash
curl --silent --show-error \
  --header "Authorization: Token $BUILDKITE_AGENT_TOKEN" \
  --header "Content-Type: application/json" \
  --request POST \
  https://agent.buildkite.com/v3/stacks/register \
  --data "{
    \"key\": \"$STACK_KEY\",
    \"type\": \"custom\",
    \"queue_key\": \"$QUEUE_KEY\",
    \"metadata\": {
      \"runtime\": \"docker\",
      \"environment\": \"local-lab\",
      \"version\": \"1\"
    }
  }" |
jq .
```

A new stack returns `201 Created`; updating the same stack returns `200 OK`.
The response includes the stack ID, organization UUID, queue key, metadata,
and connection state.

## 2. Discover scheduled jobs

Start a build of the disposable pipeline. Its command job should remain in
the Scheduled state because nothing is polling its queue.

List up to ten scheduled jobs, oldest first:

```bash
curl --silent --show-error \
  --header "Authorization: Token $BUILDKITE_AGENT_TOKEN" \
  "https://agent.buildkite.com/v3/stacks/$STACK_KEY/scheduled-jobs?queue_key=$QUEUE_KEY&limit=10" |
jq .
```

The response can be used to test:

- queue demand and autoscaling decisions;
- priority-aware scheduling;
- routing by pipeline, branch, step key, or agent query rules;
- queue pause handling through `cluster_queue.dispatch_paused`; and
- cursor pagination through `page_info`.

Polling this endpoint also keeps the queue connection shown as Connected in
Buildkite. If a stack stops polling for roughly five to six minutes, Buildkite
shows the queue as Disconnected.

Capture the oldest scheduled job UUID:

```bash
export JOB_UUID="$(
  curl --silent --show-error \
    --header "Authorization: Token $BUILDKITE_AGENT_TOKEN" \
    "https://agent.buildkite.com/v3/stacks/$STACK_KEY/scheduled-jobs?queue_key=$QUEUE_KEY&limit=1" |
  jq --raw-output '.jobs[0].id'
)"

if [[ -z "$JOB_UUID" || "$JOB_UUID" == "null" ]]; then
  echo "No scheduled job found" >&2
else
  echo "Selected job: $JOB_UUID"
fi
```

## 3. Inspect a job without exposing its environment

The list endpoint returns scheduling metadata. The individual job endpoint
also returns the command and complete environment, which can be large and may
contain sensitive information.

This filtered request displays the command and environment variable names,
but not their values:

```bash
curl --silent --show-error \
  --header "Authorization: Token $BUILDKITE_AGENT_TOKEN" \
  "https://agent.buildkite.com/v3/stacks/$STACK_KEY/jobs/$JOB_UUID" |
jq '{
  id,
  command,
  environment_variable_names: (.env | keys)
}'
```

This endpoint supports advanced scheduling decisions, such as selecting an
image, region, machine type, CPU limit, or security boundary based on job
details.

## 4. Add a provisioning notification

Stack notifications appear on the Buildkite build page and explain what the
controller is doing while a worker is being provisioned.

```bash
curl --silent --show-error \
  --header "Authorization: Token $BUILDKITE_AGENT_TOKEN" \
  --header "Content-Type: application/json" \
  --request POST \
  "https://agent.buildkite.com/v3/stacks/$STACK_KEY/notifications" \
  --data "{
    \"notifications\": [{
      \"job_uuid\": \"$JOB_UUID\",
      \"detail\": \"Custom stack is preparing a local Docker agent\"
    }]
  }" |
jq .
```

Useful messages include `Waiting for capacity`, `Pulling worker image`, and
`Starting Docker agent`. A job can have at most 100 stack notifications, so
send them only for meaningful state changes.

## 5. Reserve the job

Reservation prevents multiple controller instances from launching the same
job. The following reservation expires after five minutes if the controller
does not renew it or start the worker:

```bash
curl --silent --show-error \
  --header "Authorization: Token $BUILDKITE_AGENT_TOKEN" \
  --header "Content-Type: application/json" \
  --request PUT \
  "https://agent.buildkite.com/v3/stacks/$STACK_KEY/scheduled-jobs/batch-reserve" \
  --data "{
    \"job_uuids\": [\"$JOB_UUID\"],
    \"reservation_expiry_seconds\": 300
  }" |
jq .
```

Expected response:

```json
{
  "reserved": ["<job-uuid>"],
  "not_reserved": []
}
```

Only start workers for UUIDs returned in `reserved`. A second controller
attempting to reserve the same job should receive it in `not_reserved`.

Two useful variations are:

- Do not start a worker and confirm the job reappears after the reservation
  expires.
- Attempt the reservation from two terminals and confirm only one controller
  wins.

## 6. Check job state while provisioning

A controller should recheck state while waiting for compute capacity. If a
user cancels the job before the Docker container starts, the controller can
avoid launching unnecessary infrastructure.

```bash
curl --silent --show-error \
  --header "Authorization: Token $BUILDKITE_AGENT_TOKEN" \
  --header "Content-Type: application/json" \
  --request POST \
  "https://agent.buildkite.com/v3/stacks/$STACK_KEY/jobs/get-states" \
  --data "{\"job_uuids\":[\"$JOB_UUID\"]}" |
jq .
```

For a cancellation test, reserve a new disposable job, cancel it in the
Buildkite UI, call this endpoint, and confirm its state changed. Do not request
a JAT or start a container for a canceled job.

## 7. Issue a Job Acquisition Token

Request the JAT only after the job is successfully reserved and Docker
capacity is available. This example creates a five-minute token:

```bash
JAT_RESPONSE="$(
  curl --silent --show-error \
    --header "Authorization: Token $BUILDKITE_AGENT_TOKEN" \
    --header "Content-Type: application/json" \
    --request POST \
    "https://agent.buildkite.com/v3/stacks/$STACK_KEY/job-acquisition-tokens" \
    --data "{
      \"job_uuids\": [\"$JOB_UUID\"],
      \"token_lifetime_seconds\": 300
    }"
)"

export JOB_ACQUISITION_TOKEN="$(
  jq --raw-output \
    --arg job "$JOB_UUID" \
    '.job_acquisition_tokens[]
     | select(.job_uuid == $job)
     | .job_acquisition_token' \
    <<<"$JAT_RESPONSE"
)"

if [[ "$JOB_ACQUISITION_TOKEN" != bkjat_* ]]; then
  echo "A JAT was not issued for $JOB_UUID" >&2
  jq '{not_issued}' <<<"$JAT_RESPONSE"
fi
```

A JAT can register an ephemeral agent only for its associated reserved job.
The default lifetime is 15 minutes and the maximum is one hour, but it expires
earlier if the reservation expires. Treat it as a bearer credential: do not
print it, persist it, or expose it to job command containers unnecessarily.

## 8. Start the one-shot Docker agent

Pass the JAT, not the controller's cluster token, to the worker container:

```bash
BUILDKITE_AGENT_TOKEN="$JOB_ACQUISITION_TOKEN" \
docker run --rm \
  --name "buildkite-stack-${JOB_UUID%%-*}" \
  --volume /var/run/docker.sock:/var/run/docker.sock \
  --volume buildkite-acquire-builds:/buildkite/builds \
  --env BUILDKITE_AGENT_TOKEN \
  --env BUILDKITE_WRITE_JOB_LOGS_TO_STDOUT=true \
  ao-buildkite-acquire-agent:local \
  start \
  --acquire-job "$JOB_UUID" \
  --queue "$QUEUE_KEY" \
  --reflect-exit-status
```

The agent registers with the JAT, acquires only `JOB_UUID`, executes it, and
disconnects. Because `--rm` is set, Docker removes the container afterward.
The process exit status reflects the Buildkite job status.

Clear the JAT material from the shell after the agent starts or exits:

```bash
unset JOB_ACQUISITION_TOKEN JAT_RESPONSE
```

## 9. Report a provisioning failure

Use the finish endpoint when the stack cannot start an agent, for example
because Docker is unavailable, an image cannot be pulled, or capacity is
exhausted.

**This operation deliberately finishes the job. Use a new disposable job and
do not run it against work that should execute normally.**

```bash
curl --silent --show-error \
  --header "Authorization: Token $BUILDKITE_AGENT_TOKEN" \
  --header "Content-Type: application/json" \
  --request POST \
  "https://agent.buildkite.com/v3/stacks/$STACK_KEY/jobs/$JOB_UUID/finish" \
  --data '{
    "exit_status": -1,
    "detail": "Custom stack could not connect to the Docker daemon"
  }' |
jq .
```

Buildkite displays the detail as a stack failure on the build page. A stack
can call this endpoint at most once for a job. Automatic retry rules may apply
to stack failures, so use a disposable pipeline without retries for this test.

## 10. Inspect rate-limit headers

Each Stacks API endpoint has an independent per-stack rate limit. Inspect the
headers without printing credentials:

```bash
curl --silent --show-error \
  --dump-header /tmp/buildkite-stack-headers \
  --output /tmp/buildkite-stack-response.json \
  --header "Authorization: Token $BUILDKITE_AGENT_TOKEN" \
  "https://agent.buildkite.com/v3/stacks/$STACK_KEY/scheduled-jobs?queue_key=$QUEUE_KEY&limit=1"

grep -i '^ratelimit-' /tmp/buildkite-stack-headers
jq . /tmp/buildkite-stack-response.json
```

A controller should retry `429`, network failures, and `5xx` responses using
bounded exponential backoff with jitter, and honor `Retry-After` when present.
It should not blindly retry other `4xx` responses.

## 11. Deregister the stack

Deregister the lab controller after testing so it does not consume an active
stack slot:

```bash
curl --silent --show-error \
  --header "Authorization: Token $BUILDKITE_AGENT_TOKEN" \
  --request POST \
  "https://agent.buildkite.com/v3/stacks/$STACK_KEY/deregister" \
  --write-out 'HTTP %{http_code}\n' \
  --output /dev/null
```

Expected status: `204`.

## Additional use cases

Once the manual flow works, the same endpoints support:

- **Autoscaling:** convert scheduled job count into Docker, VM, or cloud task
  capacity.
- **Custom placement:** route jobs according to branch, pipeline, priority,
  command, environment, region, hardware, or compliance requirements.
- **High availability:** run multiple controllers and use reservations so only
  one provisions each job.
- **Batch scheduling:** reserve and launch groups of jobs, up to the documented
  endpoint limits.
- **Cancellation-aware provisioning:** stop image pulls or VM creation when a
  reserved job becomes canceled.
- **Secure ephemeral workers:** retain the long-lived `bkct_` token in the
  controller and give each worker only a short-lived `bkjat_` token.
- **Capacity waiting:** add stack notifications while waiting for a scarce GPU,
  concurrency slot, or deployment environment.
- **Infrastructure feedback:** finish jobs promptly when the worker cannot be
  created instead of leaving users waiting for a timeout.
- **Queue health:** use regular scheduled-job polling to reflect whether the
  custom stack is connected and observe whether dispatch is paused.

## Troubleshooting

| Symptom | Likely cause | Check |
| --- | --- | --- |
| `401 Unauthorized` | Missing, expired, or wrong credential | Use a `bkct_` cluster agent token and the `Token` authorization scheme |
| `403 Forbidden` | Organization stack limit reached | Deregister unused lab stacks |
| `404 Not Found` | Stack, queue, or job is not visible to the token | Confirm stack key, queue key, cluster, and job UUID |
| `422 Unprocessable Entity` | Invalid request fields | Inspect the response `message` and payload types |
| Empty `jobs` array | Nothing is waiting or another agent acquired it | Stop competing agents/controllers and trigger a fresh lab build |
| Job appears in `not_reserved` | Another controller reserved it or its state changed | Query job state and do not launch a worker |
| Job appears in `not_issued` | Job is not reserved, reservation expired, or job is ineligible | Reserve it successfully, then request the JAT immediately |
| Queue looks disconnected | Controller stopped polling | Poll scheduled jobs regularly while the controller is healthy |
| Docker agent cannot build images | Docker socket or CLI unavailable | Build the provided agent image and mount `/var/run/docker.sock` |

## References

- [Buildkite Stacks API](https://buildkite.com/docs/apis/agent-api/stacks)
- [Buildkite Job Acquisition Tokens](https://buildkite.com/docs/agent/self-hosted/job-acquisition-tokens)
- [Buildkite agent `start` command](https://buildkite.com/docs/agent/cli/reference/start)
- [Running the Buildkite agent with Docker](https://buildkite.com/docs/agent/v3/docker)
