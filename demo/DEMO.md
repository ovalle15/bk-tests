# Buildkite 101 Demo: From Commit to Green Build (10 minutes)

Matches slide 29 of "Abridge - Buildkite 101": **Commit + push → Pipeline upload → API tests → Green build**.

- Pipeline: https://buildkite.com/spacecamp/ao-demo (branch `ao-demo`)
- Agents: Buildkite **hosted macOS**, queue `macos-medium` (Apple M4 Pro, macOS 26, Xcode 26)
- App: `demo/api/server.py`, a small Notes API (Python standard library, no install step)
- One build takes about 1 minute to reach the approval gate.

## Pipeline shape

```
:pipeline: Upload pipeline        (UI step, runs `buildkite-agent pipeline upload` on macos-medium)
 └─ .buildkite/pipeline.yml
    ├─ group ":mag: Checks"
    │   ├─ :apple: Hosted agent info  → info annotation (macOS, M4, Xcode)
    │   └─ :python: Lint
    ├─ :test_tube: API tests   (depends_on lint)  → pass/fail annotation + junit.xml artifact
    ├─ :package: Build         (depends_on lint)  → dist/notes-api.tar.gz artifact
    ├─ block ":rocket: Deploy to staging?"        (depends_on api-tests + build)
    └─ :rocket: Deploy (staging)  concurrency: 1, concurrency_group: ao-demo/deploy/staging
```

## Before the call (2 minutes)

1. `git pull` on `ao-demo`. The working copy should have the bug in `demo/api/server.py`
   (a missing note returns `200` with `null`).
2. Open the latest **red** build on ao-demo in one tab, and `demo/api/server.py` in your editor.
3. Optional warm-up: `bash demo/scripts/run-api-tests.sh` locally shows the same failing test.

## Run of show

| Time | What you do | What you say (slide link) |
|---|---|---|
| 0:00–1:00 | Show the pipeline settings: the single UI step with `queue: macos-medium`. Then open `.buildkite/pipeline.yml`. | "The UI only holds the upload step. The real pipeline lives in the repo." (slides 12, 14) |
| 1:00–2:30 | Walk the YAML: top-level `agents: queue`, the group, `depends_on`, `artifact_paths`, block step, `concurrency_group`. | Steps run in parallel by default, ordering only where needed. (slides 13, 18, 21) |
| 2:30–4:00 | Open the red build. Point at the **agent info** annotation (hosted M4, clean machine). Then the **red API tests annotation**: expand `test_get_missing_note_returns_404`. | "Logs are the full record; annotations are the curated summary." (slides 3, 25) |
| 4:00–4:45 | Click into the API tests job log: the `+++ Running API tests` section is auto-expanded and the server log shows `GET /notes/999 200`. Show **Artifacts** tab (junit.xml, server.log). Note Deploy never ran (`waiting_failed`). | Artifacts pass files between jobs. (slide 24) |
| 4:45–5:30 | **Live fix** in `demo/api/server.py` (see below), then commit and push. | "Push sends a webhook, which creates the build." (slide 14) |
| 5:30–7:00 | Watch the new build: the upload job adds steps, jobs land on `macos-medium`, Lint/Build/API tests run. Show the green annotation. | Routing: cluster → queue → agent. (slides 11, 16) |
| 7:00–8:30 | Build pauses at **Deploy to staging?** Click it, approve. Deploy job downloads the Build artifact and posts a success annotation with who approved it. | Human gate + concurrency group = one deploy at a time. (slides 18, 19) |
| 8:30–10:00 | Build is **green**. Recap: commit → upload → routed to hosted macOS → annotated results → gated deploy. Q&A. | Wrap-up (slide 30) |

## The live fix

In `demo/api/server.py`, inside `do_GET`:

```python
# before (bug)
            if note is None:
                return self._send(200, None)

# after (fix)
            if note is None:
                return self._send(404, {"error": "note not found"})
```

```bash
git commit -am "Return 404 for missing notes" && git push
```

## Reset after the demo

Put the bug back so the next run starts red:

```bash
git revert --no-edit HEAD && git push
```

## Talking points if there is extra time

- **Retry**: retry the failed API tests job from the red build to show retries are per job.
- **Terminal access**: macOS hosted jobs support terminal and desktop access for debugging (slide 4).
- **Parallelism**: add `parallelism: 3` to API tests to show one step creating three jobs (slide 21).
- **MCP**: ask Claude, via the Buildkite MCP server, "why did the last ao-demo build fail?" (slide 19).
