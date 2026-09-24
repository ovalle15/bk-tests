#!/usr/bin/env python3
"""Resolve Buildkite queues by workload and agent-hosting type.

The routing policy lives in .buildkite/queue-routing.json so changing where a
workload runs does not require changing pipeline-generation code.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / ".buildkite" / "queue-routing.json"
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class QueueConfigurationError(ValueError):
    """Raised when the queue-routing policy is incomplete or invalid."""


@dataclass(frozen=True)
class QueueRoute:
    workload: str
    queue_type: str
    queue: str
    source: str

    def as_dict(self) -> dict[str, str]:
        return {
            "workload": self.workload,
            "queue_type": self.queue_type,
            "queue": self.queue,
            "source": self.source,
        }


class QueueRouter:
    """Load, validate, and resolve the queue-routing policy."""

    def __init__(
        self,
        config: Mapping[str, Any],
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self.config = config
        self.environment = os.environ if environment is None else environment
        self.validate_structure()

    @classmethod
    def from_file(
        cls,
        path: Path = DEFAULT_CONFIG,
        environment: Mapping[str, str] | None = None,
    ) -> "QueueRouter":
        try:
            with path.open(encoding="utf-8") as config_file:
                config = json.load(config_file)
        except FileNotFoundError as error:
            raise QueueConfigurationError(f"Queue config not found: {path}") from error
        except json.JSONDecodeError as error:
            raise QueueConfigurationError(
                f"Queue config is not valid JSON: {path}: {error}"
            ) from error
        return cls(config, environment)

    def validate_structure(self) -> None:
        queue_types = self.config.get("queue_types")
        workloads = self.config.get("workloads")
        if not isinstance(queue_types, dict) or not queue_types:
            raise QueueConfigurationError("queue_types must be a non-empty object")
        if not isinstance(workloads, dict) or not workloads:
            raise QueueConfigurationError("workloads must be a non-empty object")

        for queue_type, settings in queue_types.items():
            self._validate_name("queue type", queue_type)
            if not isinstance(settings, dict):
                raise QueueConfigurationError(
                    f"Queue type {queue_type!r} must be an object"
                )
            queue = settings.get("queue")
            if queue is not None and (not isinstance(queue, str) or not queue.strip()):
                raise QueueConfigurationError(
                    f"Queue type {queue_type!r} has an invalid queue value"
                )
            override = settings.get("environment_override")
            if override is not None and (
                not isinstance(override, str) or not override.strip()
            ):
                raise QueueConfigurationError(
                    f"Queue type {queue_type!r} has an invalid environment_override"
                )

        for workload, settings in workloads.items():
            self._validate_name("workload", workload)
            if not isinstance(settings, dict):
                raise QueueConfigurationError(
                    f"Workload {workload!r} must be an object"
                )
            queue_type = settings.get("queue_type")
            if queue_type not in queue_types:
                raise QueueConfigurationError(
                    f"Workload {workload!r} references unknown queue type "
                    f"{queue_type!r}"
                )
            queue = settings.get("queue")
            if queue is not None and (not isinstance(queue, str) or not queue.strip()):
                raise QueueConfigurationError(
                    f"Workload {workload!r} has an invalid queue override"
                )

    @staticmethod
    def _validate_name(kind: str, name: object) -> None:
        if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
            raise QueueConfigurationError(f"Invalid {kind} name: {name!r}")

    def resolve(self, workload: str) -> QueueRoute:
        workloads = self.config["workloads"]
        if workload not in workloads:
            choices = ", ".join(sorted(workloads))
            raise QueueConfigurationError(
                f"Unknown workload {workload!r}. Configured workloads: {choices}"
            )

        workload_settings = workloads[workload]
        queue_type = workload_settings["queue_type"]
        type_settings = self.config["queue_types"][queue_type]

        # A workload-specific environment variable has highest priority. This
        # supports temporary rerouting without committing a policy change.
        workload_override = "BUILDKITE_QUEUE_WORKLOAD_" + re.sub(
            r"[^A-Z0-9]", "_", workload.upper()
        )
        if self.environment.get(workload_override):
            return QueueRoute(
                workload, queue_type, self.environment[workload_override], workload_override
            )

        # A queue-type override reroutes every workload of that type together.
        type_override = type_settings.get("environment_override")
        if type_override and self.environment.get(type_override):
            return QueueRoute(
                workload, queue_type, self.environment[type_override], type_override
            )

        if workload_settings.get("queue"):
            return QueueRoute(
                workload,
                queue_type,
                workload_settings["queue"],
                f"workloads.{workload}.queue",
            )

        if type_settings.get("queue"):
            return QueueRoute(
                workload,
                queue_type,
                type_settings["queue"],
                f"queue_types.{queue_type}.queue",
            )

        raise QueueConfigurationError(
            f"No queue is configured for workload {workload!r} (type "
            f"{queue_type!r}). Set {type_override or 'a queue in the config'}."
        )

    def resolve_all(self) -> list[QueueRoute]:
        return [self.resolve(workload) for workload in self.config["workloads"]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve Buildkite queues from the shared routing policy."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"routing config (default: {DEFAULT_CONFIG})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve_parser = subparsers.add_parser(
        "resolve", help="print the queue key for one workload"
    )
    resolve_parser.add_argument("workload")

    subparsers.add_parser("list", help="print all effective workload routes")
    subparsers.add_parser("validate", help="validate every configured route")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        router = QueueRouter.from_file(args.config)
        if args.command == "resolve":
            print(router.resolve(args.workload).queue)
        elif args.command == "list":
            print(json.dumps([route.as_dict() for route in router.resolve_all()], indent=2))
        else:
            routes = router.resolve_all()
            print(f"Valid queue routing configuration ({len(routes)} workloads)")
    except QueueConfigurationError as error:
        print(f"queue-router: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
