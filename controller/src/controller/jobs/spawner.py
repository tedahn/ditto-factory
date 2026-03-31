from __future__ import annotations
import time
from kubernetes import client as k8s
from controller.config import Settings
from controller.loadout import AgentLoadout
from controller.models import ResourceProfile

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

    def _create_loadout_configmap(self, thread_id: str, loadout: AgentLoadout) -> str | None:
        """Create a K8s ConfigMap containing toolkit files to mount."""
        if not loadout.mounted_files:
            return None

        short_id = self._sanitize_label(thread_id[:8])
        ts = int(time.time())
        cm_name = f"df-loadout-{short_id}-{ts}"

        # K8s ConfigMap keys can't have / in them, so we flatten paths
        # Key format: path with / replaced by __ and . replaced by _dot_
        data = {}
        for path, content in loadout.mounted_files.items():
            key = path.replace("/", "__").replace(".", "_dot_")
            data[key] = content

        configmap = k8s.V1ConfigMap(
            metadata=k8s.V1ObjectMeta(
                name=cm_name,
                labels={"app": "ditto-factory", "thread-id": short_id},
            ),
            data=data,
        )

        core_api = k8s.CoreV1Api()
        core_api.create_namespaced_config_map(namespace=self._namespace, body=configmap)
        return cm_name

    def cleanup_loadout_configmap(self, thread_id: str) -> None:
        """Delete ConfigMaps created for a thread's loadout."""
        short_id = self._sanitize_label(thread_id[:8])
        core_api = k8s.CoreV1Api()
        try:
            cms = core_api.list_namespaced_config_map(
                namespace=self._namespace,
                label_selector=f"app=ditto-factory,thread-id={short_id}",
            )
            for cm in cms.items:
                core_api.delete_namespaced_config_map(
                    name=cm.metadata.name, namespace=self._namespace
                )
        except Exception:
            pass  # Best-effort cleanup

    def build_job_spec(
        self,
        thread_id: str,
        github_token: str,
        redis_url: str,
        agent_image: str | None = None,
        extra_env: dict[str, str] | None = None,
        resource_profile: ResourceProfile | None = None,
        loadout: AgentLoadout | None = None,
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

        # Add loadout env vars
        if loadout and loadout.env_vars:
            for key, value in loadout.env_vars.items():
                env_vars.append(k8s.V1EnvVar(name=key, value=str(value)))

        # Add CLAUDE.md additions as env var for entrypoint to append
        if loadout and loadout.claude_md_additions:
            combined = "\n\n".join(loadout.claude_md_additions)
            env_vars.append(k8s.V1EnvVar(
                name="DITTO_CLAUDE_MD_ADDITIONS",
                value=combined,
            ))

        # Resolve resource requests/limits
        if resource_profile:
            cpu_req = resource_profile.cpu_request
            cpu_lim = resource_profile.cpu_limit
            mem_req = resource_profile.memory_request
            mem_lim = resource_profile.memory_limit
        else:
            cpu_req = self._settings.agent_cpu_request
            cpu_lim = self._settings.agent_cpu_limit
            mem_req = self._settings.agent_memory_request
            mem_lim = self._settings.agent_memory_limit

        # Build volume mounts for loadout toolkit files
        volumes: list[k8s.V1Volume] = []
        volume_mounts: list[k8s.V1VolumeMount] = []

        if loadout and loadout.mounted_files:
            cm_name = self._create_loadout_configmap(thread_id, loadout)
            if cm_name:
                # Mount each file to its correct path inside the ConfigMap volume
                items = []
                for path, _content in loadout.mounted_files.items():
                    key = path.replace("/", "__").replace(".", "_dot_")
                    items.append(k8s.V1KeyToPath(key=key, path=path))

                volumes.append(k8s.V1Volume(
                    name="toolkit-loadout",
                    config_map=k8s.V1ConfigMapVolumeSource(
                        name=cm_name,
                        items=items,
                    ),
                ))
                volume_mounts.append(k8s.V1VolumeMount(
                    name="toolkit-loadout",
                    mount_path="/workspace/.toolkit",
                    read_only=True,
                ))

        container = k8s.V1Container(
            name="agent",
            image=image,
            image_pull_policy=self._settings.image_pull_policy,
            env=env_vars,
            resources=k8s.V1ResourceRequirements(
                requests={"cpu": cpu_req, "memory": mem_req},
                limits={"cpu": cpu_lim, "memory": mem_lim},
            ),
            security_context=k8s.V1SecurityContext(
                run_as_non_root=True,
                run_as_user=1000,
                allow_privilege_escalation=False,
                capabilities=k8s.V1Capabilities(drop=["ALL"]),
            ),
            volume_mounts=volume_mounts or None,
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
                        volumes=volumes or None,
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
        resource_profile: ResourceProfile | None = None,
        loadout: AgentLoadout | None = None,
    ) -> str:
        job = self.build_job_spec(
            thread_id, github_token, redis_url,
            agent_image=agent_image, extra_env=extra_env,
            resource_profile=resource_profile, loadout=loadout,
        )
        self._batch_api.create_namespaced_job(namespace=self._namespace, body=job)
        return job.metadata.name

    def delete(self, job_name: str) -> None:
        self._batch_api.delete_namespaced_job(
            name=job_name,
            namespace=self._namespace,
            body=k8s.V1DeleteOptions(propagation_policy="Foreground"),
        )

    def list_agent_pods(self) -> list[dict]:
        """List all ditto-factory agent pods with their status."""
        core_api = k8s.CoreV1Api()
        pods = core_api.list_namespaced_pod(
            namespace=self._namespace,
            label_selector="app=ditto-factory",
        )
        result = []
        for pod in pods.items:
            container_status = None
            if pod.status.container_statuses:
                cs = pod.status.container_statuses[0]
                if cs.state.running:
                    container_status = "running"
                elif cs.state.terminated:
                    container_status = "completed" if cs.state.terminated.exit_code == 0 else "failed"
                elif cs.state.waiting:
                    container_status = cs.state.waiting.reason or "waiting"

            # Extract thread_id from env vars
            thread_id = ""
            if pod.spec.containers:
                for env in (pod.spec.containers[0].env or []):
                    if env.name == "THREAD_ID":
                        thread_id = env.value or ""
                        break

            result.append({
                "name": pod.metadata.name,
                "status": container_status or pod.status.phase or "unknown",
                "phase": pod.status.phase,
                "thread_id": thread_id,
                "image": pod.spec.containers[0].image if pod.spec.containers else "",
                "started_at": pod.status.start_time.isoformat() if pod.status.start_time else None,
                "node": pod.spec.node_name,
            })
        return result
