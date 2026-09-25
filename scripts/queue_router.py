#!/usr/bin/env python3
"""Resolve Buildkite queues for pipelines and generated workloads."""

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
ROUTE_GROUPS = {"pipeline": "pipelines", "workload": "workloads"}


class QueueConfigurationError(ValueError):
    """Raised when the queue-routing policy is incomplete or invalid."""


@dataclass(frozen=True)
class QueueRoute:
    target_kind: str
    target: str
    queue_name: str
    queue_type: str
    queue: str
    source: str

    def as_dict(self) -> dict[str, str]:
        return {
            "target_kind": self.target_kind,
            "target": self.target,
            "queue_name": self.queue_name,
            "queue_type": self.queue_type,
            "queue": self.queue,
            "source": self.source,
        }


class QueueRouter:
    """Load, validate, and resolve the shared queue-routing policy."""

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
        queues = self.config.get("queues")
        if not isinstance(queues, dict) or not queues:
            raise QueueConfigurationError("queues must be a non-empty object")

        for queue_name, settings in queues.items():
            self._validate_name("queue", queue_name)
            if not isinstance(settings, dict):
                raise QueueConfigurationError(
                    f"Queue {queue_name!r} must be an object"
                )
            queue_key = settings.get("key")
            if not isinstance(queue_key, str) or not queue_key.strip():
                raise QueueConfigurationError(
                    f"Queue {queue_name!r} must have a non-empty key"
                )
            queue_type = settings.get("type")
            if queue_type not in {"hosted", "self_hosted"}:
                raise QueueConfigurationError(
                    f"Queue {queue_name!r} must have type 'hosted' or 'self_hosted'"
                )
            override = settings.get("environment_override")
            if override is not None and (
                not isinstance(override, str) or not override.strip()
            ):
                raise QueueConfigurationError(
                    f"Queue {queue_name!r} has an invalid environment_override"
                )

        for group_name in ROUTE_GROUPS.values():
            routes = self.config.get(group_name)
            if not isinstance(routes, dict) or not routes:
                raise QueueConfigurationError(
                    f"{group_name} must be a non-empty object"
                )
            for target, settings in routes.items():
                self._validate_name(group_name[:-1], target)
                if not isinstance(settings, dict):
                    raise QueueConfigurationError(
                        f"{group_name[:-1].title()} {target!r} must be an object"
                    )
                queue_name = settings.get("queue")
                if queue_name not in queues:
                    raise QueueConfigurationError(
                        f"{group_name[:-1].title()} {target!r} references unknown "
                        f"queue {queue_name!r}"
                    )

    @staticmethod
    def _validate_name(kind: str, name: object) -> None:
        if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
            raise QueueConfigurationError(f"Invalid {kind} name: {name!r}")

    @staticmethod
    def _override_name(target_kind: str, target: str) -> str:
        normalized_target = re.sub(r"[^A-Z0-9]", "_", target.upper())
        return f"BUILDKITE_QUEUE_{target_kind.upper()}_{normalized_target}"

    def _resolve(self, target_kind: str, target: str) -> QueueRoute:
        group_name = ROUTE_GROUPS[target_kind]
        routes = self.config[group_name]
        if target not in routes:
            choices = ", ".join(sorted(routes))
            raise QueueConfigurationError(
                f"Unknown {target_kind} {target!r}. Configured {group_name}: {choices}"
            )

        queue_name = routes[target]["queue"]
        queue_settings = self.config["queues"][queue_name]
        queue_key = queue_settings["key"]
        source = f"{group_name}.{target}.queue"

        # A target-specific override has the highest priority.
        target_override = self._override_name(target_kind, target)
        if self.environment.get(target_override):
            queue_key = self.environment[target_override]
            source = target_override
        else:
            # A catalog-level override reroutes every target using that queue.
            queue_override = queue_settings.get("environment_override")
            if queue_override and self.environment.get(queue_override):
                queue_key = self.environment[queue_override]
                source = queue_override

        return QueueRoute(
            target_kind=target_kind,
            target=target,
            queue_name=queue_name,
            queue_type=queue_settings["type"],
            queue=queue_key,
            source=source,
        )

    def resolve(self, workload: str) -> QueueRoute:
        """Resolve a workload; retained as the generator's concise API."""
        return self.resolve_workload(workload)

    def resolve_workload(self, workload: str) -> QueueRoute:
        return self._resolve("workload", workload)

    def resolve_pipeline(self, pipeline: str) -> QueueRoute:
        return self._resolve("pipeline", pipeline)

    def resolve_all(self, target_kind: str | None = None) -> list[QueueRoute]:
        kinds = [target_kind] if target_kind else list(ROUTE_GROUPS)
        return [
            self._resolve(kind, target)
            for kind in kinds
            for target in self.config[ROUTE_GROUPS[kind]]
        ]


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
        "resolve", help="print the queue key for one pipeline or workload"
    )
    resolve_parser.add_argument("name")
    resolve_parser.add_argument(
        "--kind", choices=sorted(ROUTE_GROUPS), default="workload"
    )

    list_parser = subparsers.add_parser("list", help="print effective routes")
    list_parser.add_argument(
        "--kind", choices=["all", *sorted(ROUTE_GROUPS)], default="all"
    )
    subparsers.add_parser("validate", help="validate every configured route")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        router = QueueRouter.from_file(args.config)
        if args.command == "resolve":
            route = (
                router.resolve_pipeline(args.name)
                if args.kind == "pipeline"
                else router.resolve_workload(args.name)
            )
            print(route.queue)
        elif args.command == "list":
            target_kind = None if args.kind == "all" else args.kind
            print(
                json.dumps(
                    [route.as_dict() for route in router.resolve_all(target_kind)],
                    indent=2,
                )
            )
        else:
            routes = router.resolve_all()
            print(f"Valid queue routing configuration ({len(routes)} routes)")
    except QueueConfigurationError as error:
        print(f"queue-router: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
