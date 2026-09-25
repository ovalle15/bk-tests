"""Test the Buildkite queue router and dynamic pipeline generator."""

import unittest

from generate_pipeline import generate_pipeline
from queue_router import QueueConfigurationError, QueueRouter


def config(hosted_queue=None):
    return {
        "queue_types": {
            "hosted": {
                "queue": hosted_queue,
                "environment_override": "BUILDKITE_QUEUE_HOSTED",
            },
            "self_hosted": {
                "queue": "kube",
                "environment_override": "BUILDKITE_QUEUE_SELF_HOSTED",
            },
        },
        "workloads": {
            "record-cluster": {"queue_type": "self_hosted"},
            "unit": {"queue_type": "hosted"},
            "integration": {"queue_type": "self_hosted"},
            "e2e": {"queue_type": "self_hosted", "queue": "eks"},
            "summary": {"queue_type": "self_hosted"},
        },
    }


class QueueRouterTests(unittest.TestCase):
    def test_uses_queue_type_default(self):
        router = QueueRouter(config(hosted_queue="hosted-linux"), environment={})

        route = router.resolve("unit")

        self.assertEqual(route.queue, "hosted-linux")
        self.assertEqual(route.queue_type, "hosted")

    def test_queue_type_environment_override(self):
        router = QueueRouter(
            config(hosted_queue="hosted-linux"),
            environment={"BUILDKITE_QUEUE_SELF_HOSTED": "eks"},
        )

        self.assertEqual(router.resolve("integration").queue, "eks")

    def test_workload_environment_override_has_highest_priority(self):
        router = QueueRouter(
            config(hosted_queue="hosted-linux"),
            environment={
                "BUILDKITE_QUEUE_SELF_HOSTED": "kube",
                "BUILDKITE_QUEUE_WORKLOAD_E2E": "webhook-acquire",
            },
        )

        route = router.resolve("e2e")

        self.assertEqual(route.queue, "webhook-acquire")
        self.assertEqual(route.source, "BUILDKITE_QUEUE_WORKLOAD_E2E")

    def test_missing_queue_fails_instead_of_using_default_queue(self):
        router = QueueRouter(config(), environment={})

        with self.assertRaisesRegex(QueueConfigurationError, "No queue is configured"):
            router.resolve("unit")

    def test_generated_command_steps_all_have_an_explicit_queue(self):
        router = QueueRouter(config(hosted_queue="hosted-linux"), environment={})

        pipeline = generate_pipeline("main", router)

        self.assertTrue(pipeline["steps"])
        command_steps = [step for step in pipeline["steps"] if "command" in step]
        for step in command_steps:
            self.assertTrue(step["agents"]["queue"])

    def test_trigger_passes_queue_to_the_triggered_build(self):
        router = QueueRouter(config(hosted_queue="hosted-linux"), environment={})

        pipeline = generate_pipeline("main", router)
        trigger = next(step for step in pipeline["steps"] if "trigger" in step)

        self.assertEqual(trigger["build"]["env"]["QUEUE"], "kube")
        self.assertNotIn("agents", trigger)


if __name__ == "__main__":
    unittest.main()
