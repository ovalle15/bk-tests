#!/usr/bin/env python3
"""Launch one-shot Docker Buildkite agents from Webhook.site job events."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value else default


def request_header(request: dict, name: str) -> str | None:
    for key, value in request.get("headers", {}).items():
        if key.lower() != name.lower():
            continue
        if isinstance(value, list):
            return str(value[0]) if value else None
        return str(value)
    return None


def verify_webhook(request: dict, raw_body: str, secret: str | None) -> bool:
    if not secret:
        return True

    signature_header = request_header(request, "x-buildkite-signature")
    if signature_header:
        parts = dict(
            part.strip().split("=", 1)
            for part in signature_header.split(",")
            if "=" in part
        )
        timestamp = parts.get("timestamp")
        signature = parts.get("signature")
        if not timestamp or not signature:
            return False
        try:
            if abs(time.time() - int(timestamp)) > 300:
                return False
        except ValueError:
            return False
        signed_body = f"{timestamp}.{raw_body}".encode()
        expected = hmac.new(secret.encode(), signed_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    token = request_header(request, "x-buildkite-token")
    return token is not None and hmac.compare_digest(token, secret)


def fetch_requests(token: str, api_key: str | None) -> list[dict]:
    query = urllib.parse.urlencode({"sorting": "newest", "per_page": 100})
    url = f"https://webhook.site/token/{token}/requests?{query}"
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Api-Key"] = api_key
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15) as response:
        document = json.load(response)
    return document.get("data", [])


def matches_target(payload: dict, queue: str, pipeline_slug: str | None) -> bool:
    if payload.get("event") != "job.scheduled":
        return False
    job = payload.get("job") or {}
    if job.get("type") not in (None, "script"):
        return False
    rules = job.get("agent_query_rules") or []
    if f"queue={queue}" not in rules:
        return False
    if pipeline_slug and (payload.get("pipeline") or {}).get("slug") != pipeline_slug:
        return False
    return bool(job.get("id"))


def agent_command(job_id: str, queue: str, image: str) -> list[str]:
    suffix = job_id.replace("-", "")[:12]
    agent_name = f"{socket.gethostname()}-docker-acquire-{suffix}"
    return [
        "docker",
        "run",
        "--rm",
        "--name",
        f"buildkite-acquire-{suffix}",
        "--volume",
        "/var/run/docker.sock:/var/run/docker.sock",
        "--volume",
        "buildkite-acquire-builds:/buildkite/builds",
        "--env",
        "BUILDKITE_AGENT_TOKEN",
        "--env",
        f"BUILDKITE_AGENT_NAME={agent_name}",
        "--env",
        "BUILDKITE_WRITE_JOB_LOGS_TO_STDOUT=true",
        image,
        "start",
        "--acquire-job",
        job_id,
        "--queue",
        queue,
        "--reflect-exit-status",
    ]


def reap(processes: dict[str, subprocess.Popen]) -> None:
    for job_id, process in list(processes.items()):
        status = process.poll()
        if status is not None:
            print(f"agent for job {job_id} exited with status {status}", flush=True)
            del processes[job_id]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="poll once and exit")
    parser.add_argument(
        "--replay-existing",
        action="store_true",
        help="process matching events already present when the watcher starts",
    )
    parser.add_argument("--dry-run", action="store_true", help="print docker commands only")
    parser.add_argument("--poll-interval", type=float, default=float(env("WEBHOOK_POLL_INTERVAL", "2")))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    webhook_token = env("WEBHOOK_SITE_TOKEN")
    agent_token = env("BUILDKITE_AGENT_TOKEN")
    if not webhook_token:
        print("WEBHOOK_SITE_TOKEN is required", file=sys.stderr)
        return 2
    if not agent_token and not args.dry_run:
        print("BUILDKITE_AGENT_TOKEN is required", file=sys.stderr)
        return 2

    queue = env("BUILDKITE_TARGET_QUEUE", "default-queue")
    image = env("BUILDKITE_AGENT_IMAGE", "ao-buildkite-acquire-agent:local")
    pipeline_slug = env("BUILDKITE_PIPELINE_SLUG")
    webhook_secret = env("BUILDKITE_WEBHOOK_TOKEN")
    api_key = env("WEBHOOK_SITE_API_KEY")
    seen: set[str] = set()
    processes: dict[str, subprocess.Popen] = {}
    first_poll = True

    if not webhook_secret:
        print("warning: BUILDKITE_WEBHOOK_TOKEN is unset; webhook authenticity will not be verified", flush=True)
    print(f"watching Webhook.site for queue={queue}", flush=True)

    while True:
        try:
            requests = fetch_requests(webhook_token, api_key)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
            print(f"failed to poll Webhook.site: {error}", file=sys.stderr, flush=True)
            if args.once:
                return 1
            time.sleep(args.poll_interval)
            continue

        if first_poll and not args.replay_existing:
            seen.update(str(item.get("uuid")) for item in requests if item.get("uuid"))
            first_poll = False
            print(f"ignored {len(seen)} existing request(s); waiting for new events", flush=True)
        else:
            first_poll = False
            for request in reversed(requests):
                request_id = str(request.get("uuid", ""))
                if not request_id or request_id in seen:
                    continue
                seen.add(request_id)
                raw_body = request.get("content", "")
                try:
                    payload = json.loads(raw_body)
                except (TypeError, json.JSONDecodeError):
                    continue
                if not verify_webhook(request, raw_body, webhook_secret):
                    print(f"ignored request {request_id}: webhook verification failed", file=sys.stderr, flush=True)
                    continue
                if not matches_target(payload, queue, pipeline_slug):
                    continue

                job_id = payload["job"]["id"]
                command = agent_command(job_id, queue, image)
                print(f"launching one-shot Docker agent for job {job_id}", flush=True)
                if args.dry_run:
                    print(" ".join(command), flush=True)
                else:
                    processes[job_id] = subprocess.Popen(command)

        reap(processes)
        if args.once:
            return 0
        time.sleep(args.poll_interval)


if __name__ == "__main__":
    raise SystemExit(main())
