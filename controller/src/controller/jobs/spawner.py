from __future__ import annotations
import time
from kubernetes import client as k8s
from controller.config import Settings

class JobSpawner:
    def __init__(self, settings: Settings, batch_api: k8s.BatchV1Api, namespace: str = "default"):
        self._settings = settings
        self._batch_api = batch_api
        self._namespace = namespace

    @staticmethod
    def _sanitize_label(value: str) -> str:
        """Ensure value is a valid K8s label (alphanumeric start/end, max 63 chars)."""
        sanitized = "".join(c if c.isalnum() or c in "-_." else "" for c in value)
        return sanitized.strip("-_.") or "unknown"

    def build_job_spec(
        self,
        thread_id: str,
        github_token: str,
        redis_url: str,
        agent_image: str | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> k8s.V1Job:
        short_id = self._sanitize_label(thread_id[:8])
        ts = int(time.time())
        job_name = f"df-{short_id}-{ts}"
        image = agent_image or self._settings.agent_image

        env_vars = [
            k8s.V1EnvVar(name="THREAD_ID", value=thread_id),
            k8s.V1EnvVar(name="REDIS_URL", value=redis_url),
            k8s.V1EnvVar(name="GITHUB_TOKEN", value=github_token),
            k8s.V1EnvVar(
                name="ANTHROPIC_API_KEY",
                value_from=k8s.V1EnvVarSource(
                    secret_key_ref=k8s.V1SecretKeySelector(
                        name="df-secrets", key="anthropic-api-key"
                    )
                ),
            ),
        ]

        # Add extra env vars (e.g., SUBAGENT_DEPTH for child agents)
        if extra_env:
            for key, value in extra_env.items():
                env_vars.append(k8s.V1EnvVar(name=key, value=str(value)))

        container = k8s.V1Container(
            name="agent",
            image=image,
            image_pull_policy=self._settings.image_pull_policy,
            env=env_vars,
            resources=k8s.V1ResourceRequirements(
                requests={"cpu": self._settings.agent_cpu_request, "memory": self._settings.agent_memory_request},
                limits={"cpu": self._settings.agent_cpu_limit, "memory": self._settings.agent_memory_limit},
            ),
            security_context=k8s.V1SecurityContext(
                run_as_non_root=True,
                run_as_user=1000,
                allow_privilege_escalation=False,
                capabilities=k8s.V1Capabilities(drop=["ALL"]),
            ),
        )

        return k8s.V1Job(
            metadata=k8s.V1ObjectMeta(
                name=job_name,
                labels={"app": "ditto-factory-agent", "df/thread": short_id},
            ),
            spec=k8s.V1JobSpec(
                backoff_limit=1,
                ttl_seconds_after_finished=300,
                active_deadline_seconds=self._settings.max_job_duration_seconds,
                template=k8s.V1PodTemplateSpec(
                    metadata=k8s.V1ObjectMeta(
                        labels={"app": "ditto-factory-agent", "df/thread": short_id}
                    ),
                    spec=k8s.V1PodSpec(
                        containers=[container],
                        restart_policy="Never",
                    ),
                ),
            ),
        )

    def spawn(
        self,
        thread_id: str,
        github_token: str,
        redis_url: str,
        agent_image: str | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> str:
        job = self.build_job_spec(
            thread_id, github_token, redis_url,
            agent_image=agent_image, extra_env=extra_env,
        )
        self._batch_api.create_namespaced_job(namespace=self._namespace, body=job)
        return job.metadata.name

    def delete(self, job_name: str) -> None:
        self._batch_api.delete_namespaced_job(
            name=job_name,
            namespace=self._namespace,
            body=k8s.V1DeleteOptions(propagation_policy="Foreground"),
        )
