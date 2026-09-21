# Dynamic pipeline with Python

This example adds steps to the current build at runtime. Python emits JSON,
which Buildkite accepts alongside YAML. No Python packages are required.

## Run in Buildkite

1. Commit and push these three example files.
2. In a test pipeline's YAML steps editor, configure:

   ```yaml
   steps:
     - label: ":pipeline: Upload dynamic example"
       command: buildkite-agent pipeline upload .buildkite/pipeline.dynamic.yaml
   ```

3. Ensure the selected agents have Bash, Python 3, the Buildkite agent CLI,
   and checkout access to this repository. This example uses the default
   queue. For a custom queue, add `agents: {queue: YOUR_QUEUE}` to the initial
   upload step, the generator step, and each step created by Python.
4. Start a build. Feature branches generate unit and integration demo jobs.
   `main` also generates an e2e demo job. The summary waits for every generated
   test job to pass. Test jobs can run concurrently if agents are available.

The commands only echo messages: they demonstrate scheduling, not real tests.
Replace the generated `command` with your test runner when ready.

## How it works

`pipeline.dynamic.yaml` runs `generate_pipeline.py`, saves its JSON to a
job-specific temporary file, and uploads that file. A generator failure stops
the command before upload. Uploaded steps appear in the same build.

Python selects the suites using `BUILDKITE_BRANCH` and creates explicit
dependencies for the summary. `--no-interpolation` keeps `$TEST_SUITE` intact
until each generated job runs with its own environment. The bootstrap YAML
uses `$$BUILDKITE_JOB_ID` to defer that variable until the generator job runs.

## Preview locally without uploading

```bash
BUILDKITE_BRANCH=feature/demo python3 .buildkite/generate_pipeline.py
BUILDKITE_BRANCH=main python3 .buildkite/generate_pipeline.py
```

Documentation: https://buildkite.com/docs/pipelines/configure/dynamic-pipelines
