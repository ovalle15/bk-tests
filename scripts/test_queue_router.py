"""Test the Buildkite queue router and dynamic pipeline generator."""

import unittest

from generate_pipeline import generate_pipeline
from queue_router import QueueConfigurationError, QueueRouter


def config():
    return {
        "queues": {
            "kubernetes": {
                "key": "kube",
                "type": "self_hosted",
                "environment_override": "BUILDKITE_QUEUE_KUBERNETES",
            },
            "hosted-small": {
                "key": "hosted-linux",
                "type": "hosted",
                "environment_override": "BUILDKITE_QUEUE_HOSTED_SMALL",
            },
            "deploy-agents": {
                "key": "deploy-queue",
                "type": "self_hosted",
            },
            "eks": {
                "key": "eks",
                "type": "self_hosted",
            },
        },
        "pipelines": {
            "ao-tests": {"queue": "kubernetes"},
            "ao-deploy": {"queue": "deploy-agents"},
        },
        "workloads": {
            "record-cluster": {"queue": "kubernetes"},
            "unit": {"queue": "hosted-small"},
            "integration": {"queue": "kubernetes"},
            "e2e": {"queue": "eks"},
            "summary": {"queue": "kubernetes"},
        },
    }


class QueueRouterTests(unittest.TestCase):
    def test_resolves_queue_catalog_entry(self):
        router = QueueRouter(config(), environment={})

        route = router.resolve_workload("unit")

        self.assertEqual(route.queue, "hosted-linux")
        self.assertEqual(route.queue_name, "hosted-small")
        self.assertEqual(route.queue_type, "hosted")

    def test_queue_catalog_environment_override(self):
        router = QueueRouter(
            config(),
            environment={"BUILDKITE_QUEUE_KUBERNETES": "temporary-kube"},
        )

        self.assertEqual(router.resolve_workload("integration").queue, "temporary-kube")
        self.assertEqual(router.resolve_pipeline("ao-tests").queue, "temporary-kube")

    def test_workload_environment_override_has_highest_priority(self):
        router = QueueRouter(
            config(),
            environment={
                "BUILDKITE_QUEUE_KUBERNETES": "temporary-kube",
                "BUILDKITE_QUEUE_WORKLOAD_INTEGRATION": "integration-only",
            },
        )

        route = router.resolve_workload("integration")

        self.assertEqual(route.queue, "integration-only")
        self.assertEqual(route.source, "BUILDKITE_QUEUE_WORKLOAD_INTEGRATION")

    def test_pipeline_environment_override_has_highest_priority(self):
        router = QueueRouter(
            config(),
            environment={"BUILDKITE_QUEUE_PIPELINE_AO_DEPLOY": "emergency-deploy"},
        )

        route = router.resolve_pipeline("ao-deploy")

        self.assertEqual(route.queue, "emergency-deploy")
        self.assertEqual(route.source, "BUILDKITE_QUEUE_PIPELINE_AO_DEPLOY")

    def test_unknown_queue_reference_is_rejected(self):
        invalid_config = config()
        invalid_config["pipelines"]["ao-deploy"]["queue"] = "missing"

        with self.assertRaisesRegex(QueueConfigurationError, "unknown queue"):
            QueueRouter(invalid_config, environment={})

    def test_generated_command_steps_all_have_an_explicit_queue(self):
        router = QueueRouter(config(), environment={})

        pipeline = generate_pipeline("main", router)

        self.assertTrue(pipeline["steps"])
        command_steps = [step for step in pipeline["steps"] if "command" in step]
        for step in command_steps:
            self.assertTrue(step["agents"]["queue"])

    def test_trigger_passes_pipeline_queue_to_the_triggered_build(self):
        router = QueueRouter(config(), environment={})

        pipeline = generate_pipeline("main", router)
        trigger = next(step for step in pipeline["steps"] if "trigger" in step)

        self.assertEqual(trigger["build"]["env"]["QUEUE"], "deploy-queue")
        self.assertNotIn("agents", trigger)


if __name__ == "__main__":
    unittest.main()
