"""Kubernetes worker — manages a Pod + Service in a K8s cluster."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

from ..const import (
    CONF_WORKER_EXTRA_MANIFESTS,
    CONF_WORKER_INCLUSTER,
    CONF_WORKER_KUBECONFIG,
    CONF_WORKER_MANIFEST,
    WORKER_STATUS_RUNNING,
    WORKER_STATUS_UNAVAILABLE,
)
from .base import BaseWorker

_LOGGER = logging.getLogger(__name__)
RETRY_INTERVAL = 30


def _sanitize_name(name: str) -> str:
    """Sanitize worker name for use as K8s resource name."""
    return "ha-worker-" + re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")


class KubernetesWorker(BaseWorker):
    """Worker that runs as a Kubernetes Pod + Service."""

    def __init__(self, hass: HomeAssistant, conf: dict) -> None:
        """Initialize the Kubernetes worker."""
        super().__init__(hass, conf)
        from ..config import (  # noqa: PLC0415
            CONF_WORKER_IMAGE,
            CONF_WORKER_NAMESPACE,
            CONF_WORKER_PORT,
        )

        self._namespace: str = conf[CONF_WORKER_NAMESPACE]
        self._image: str = conf[CONF_WORKER_IMAGE]
        self._port: int = conf[CONF_WORKER_PORT]
        self._incluster: bool = conf.get(CONF_WORKER_INCLUSTER, False)
        self._kubeconfig: str | None = conf.get(CONF_WORKER_KUBECONFIG)
        self._manifest_path: str | None = conf.get(CONF_WORKER_MANIFEST)
        self._extra_manifests: list[str] = conf.get(CONF_WORKER_EXTRA_MANIFESTS, [])
        self._resource_name: str = _sanitize_name(self._name)
        self._stop_on_shutdown: bool = conf.get("stop_on_shutdown", True)
        # service_type: explicit config overrides auto-detection
        # Auto-detection: NodePort if kubeconfig (external), ClusterIP if incluster
        self._service_type: str = conf.get(
            "service_type",
            "NodePort" if (self._kubeconfig and not self._incluster) else "ClusterIP",
        )
        self._stopping = False
        self._retry_task: asyncio.Task | None = None
        self._monitor_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # K8s helpers
    # ------------------------------------------------------------------

    def _get_k8s_clients(self):
        """Load K8s config and return CoreV1Api."""
        from kubernetes import client, config as k8s_config  # noqa: PLC0415

        if self._incluster:
            k8s_config.load_incluster_config()
        elif self._kubeconfig:
            k8s_config.load_kube_config(config_file=self._kubeconfig)
        else:
            k8s_config.load_kube_config()  # default ~/.kube/config

        return client.CoreV1Api()

    def _build_pod_and_service(self) -> tuple[dict, dict]:
        """Build Pod and Service manifests, optionally from a user-provided file."""
        import yaml  # noqa: PLC0415

        pod: dict | None = None
        service: dict | None = None

        if self._manifest_path:
            with open(self._manifest_path) as fh:
                docs = list(yaml.safe_load_all(fh))
            pod = next((d for d in docs if d.get("kind") == "Pod"), None)
            service = next((d for d in docs if d.get("kind") == "Service"), None)

        # Default Pod
        if pod is None:
            pod = {
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {
                    "name": self._resource_name,
                    "namespace": self._namespace,
                },
                "spec": {
                    "restartPolicy": "Always",
                    "containers": [
                        {
                            "name": "worker",
                            "image": self._image,
                            "imagePullPolicy": "Always",
                        }
                    ],
                },
            }

        # Default Service
        svc_type = self._service_type
        if service is None:
            service = {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {
                    "name": self._resource_name,
                    "namespace": self._namespace,
                },
                "spec": {
                    "type": svc_type,
                    "selector": {"app": self._resource_name},
                    "ports": [
                        {
                            "port": self._port,
                            "targetPort": self._port,
                            "name": "grpc",
                        }
                    ],
                },
            }

        # Override fields controlled by HA
        pod["metadata"]["name"] = self._resource_name
        pod["metadata"]["namespace"] = self._namespace
        pod["metadata"].setdefault("labels", {})["app"] = self._resource_name

        containers: list[dict] = pod["spec"]["containers"]
        if containers:
            containers[0]["image"] = self._image
            containers[0]["imagePullPolicy"] = "Always"
            ha_env = [
                {"name": "HA_MODE", "value": "worker"},
                {"name": "HA_WORKER_CORE_ADDRESS", "value": self._core_address},
                {"name": "HA_WORKER_PORT", "value": str(self._port)},
                {"name": "HA_WORKER_NAME", "value": self._name},
            ]
            ha_env_names = {e["name"] for e in ha_env}
            existing_env = containers[0].get("env", [])
            containers[0]["env"] = [
                e for e in existing_env if e["name"] not in ha_env_names
            ] + ha_env

        service["metadata"]["name"] = self._resource_name
        service["metadata"]["namespace"] = self._namespace
        service["spec"]["selector"] = {"app": self._resource_name}
        service["spec"]["ports"][0]["port"] = self._port
        service["spec"]["ports"][0]["targetPort"] = self._port

        return pod, service

    # ------------------------------------------------------------------
    # Synchronous K8s operations (run in executor)
    # ------------------------------------------------------------------

    def _start_sync(self) -> None:
        """Create Pod + Service synchronously."""
        import time  # noqa: PLC0415

        try:
            v1 = self._get_k8s_clients()

            # Check if pod already exists
            existing_pod = None
            try:
                existing_pod = v1.read_namespaced_pod(
                    name=self._resource_name, namespace=self._namespace
                )
            except Exception:  # noqa: BLE001
                pass

            if existing_pod is not None:
                phase = existing_pod.status.phase
                if phase == "Running":
                    _LOGGER.info(
                        "K8s worker '%s' pod already running, reusing it",
                        self._resource_name,
                    )
                    self._status = WORKER_STATUS_RUNNING
                    self._resolve_address(v1)
                    return
                _LOGGER.info(
                    "Deleting existing pod '%s' (phase=%s)",
                    self._resource_name,
                    phase,
                )
                v1.delete_namespaced_pod(
                    name=self._resource_name, namespace=self._namespace
                )
                time.sleep(2)

            pod_manifest, service_manifest = self._build_pod_and_service()

            # Create Service if it doesn't exist yet
            try:
                v1.read_namespaced_service(
                    name=self._resource_name, namespace=self._namespace
                )
                _LOGGER.debug("Service '%s' already exists", self._resource_name)
            except Exception:  # noqa: BLE001
                v1.create_namespaced_service(
                    namespace=self._namespace, body=service_manifest
                )
                _LOGGER.info("Created Service '%s'", self._resource_name)

            # Create Pod
            v1.create_namespaced_pod(namespace=self._namespace, body=pod_manifest)
            _LOGGER.info(
                "Created Pod '%s' in namespace '%s'",
                self._resource_name,
                self._namespace,
            )

            self._status = WORKER_STATUS_RUNNING
            self._resolve_address(v1)

            # Apply extra manifests
            for manifest_path in self._extra_manifests:
                self._apply_extra_manifest(manifest_path)

        except Exception as err:
            _LOGGER.error(
                "Failed to start K8s worker '%s': %s", self._name, err, exc_info=True
            )
            self._status = WORKER_STATUS_UNAVAILABLE

    def _resolve_address(self, v1) -> None:
        """Determine the gRPC address for this worker."""
        if self._service_type == "ClusterIP":
            # ClusterIP — accessible via DNS from within the cluster
            self._address = f"{self._resource_name}.{self._namespace}.svc.cluster.local:{self._port}"
            _LOGGER.debug(
                "K8s worker '%s' ClusterIP address: %s", self._name, self._address
            )
            return

        # NodePort — accessible from outside the cluster
        try:
            svc = v1.read_namespaced_service(
                name=self._resource_name, namespace=self._namespace
            )
            node_port: int | None = None
            for port in svc.spec.ports:
                if port.port == self._port and port.node_port:
                    node_port = port.node_port
                    break

            if node_port:
                nodes = v1.list_node()
                node_ip: str | None = None
                for node in nodes.items:
                    for addr in node.status.addresses:
                        if addr.type == "InternalIP":
                            node_ip = addr.address
                            break
                    if node_ip:
                        break

                if node_ip and node_port:
                    self._address = f"{node_ip}:{node_port}"
                    _LOGGER.info(
                        "K8s worker '%s' NodePort address: %s",
                        self._name,
                        self._address,
                    )
                    return
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not resolve NodePort address: %s", err)

        # Fallback
        self._address = f"localhost:{self._port}"
        _LOGGER.warning(
            "K8s worker '%s' using fallback address: %s", self._name, self._address
        )

    def _apply_extra_manifest(self, manifest_path: str) -> None:
        """Apply an extra manifest file using the K8s dynamic client."""
        import yaml  # noqa: PLC0415

        try:
            with open(manifest_path) as fh:
                docs = list(yaml.safe_load_all(fh))
            _LOGGER.info(
                "Applied extra manifest '%s' (%d document(s))",
                manifest_path,
                len(docs),
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Failed to apply manifest '%s': %s", manifest_path, err)

    def _check_running(self) -> bool:
        """Return True if the pod is in Running phase."""
        try:
            v1 = self._get_k8s_clients()
            pod = v1.read_namespaced_pod(
                name=self._resource_name, namespace=self._namespace
            )
            return pod.status.phase == "Running"
        except Exception:  # noqa: BLE001
            return False

    def _stop_sync(self) -> None:
        """Delete Pod and Service synchronously."""
        try:
            v1 = self._get_k8s_clients()
            try:
                v1.delete_namespaced_pod(
                    name=self._resource_name, namespace=self._namespace
                )
                _LOGGER.info("Deleted Pod '%s'", self._resource_name)
            except Exception:  # noqa: BLE001
                pass
            try:
                v1.delete_namespaced_service(
                    name=self._resource_name, namespace=self._namespace
                )
                _LOGGER.info("Deleted Service '%s'", self._resource_name)
            except Exception:  # noqa: BLE001
                pass
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Error stopping K8s worker '%s': %s", self._name, err)
        self._status = WORKER_STATUS_UNAVAILABLE

    # ------------------------------------------------------------------
    # Async lifecycle
    # ------------------------------------------------------------------

    async def async_start(self) -> None:
        """Start the K8s worker (create Pod + Service, then wait for readiness)."""
        self._stopping = False
        await self._hass.async_add_executor_job(self._start_sync)
        if self._status != WORKER_STATUS_RUNNING:
            if not self._stopping:
                self._schedule_retry()
            return
        # Pod created — wait for gRPC to be reachable before marking RUNNING
        self._status = WORKER_STATUS_UNAVAILABLE
        _LOGGER.info(
            "K8s worker '%s' pod created, waiting for gRPC port %d...",
            self._name,
            self._port,
        )
        asyncio.create_task(self._wait_for_ready())

    async def _wait_for_ready(self, timeout: int = 120, interval: float = 5.0) -> None:
        """Poll until the gRPC port is reachable or the timeout expires."""
        import time  # noqa: PLC0415

        deadline = time.monotonic() + timeout
        while not self._stopping and time.monotonic() < deadline:
            if await self.async_check_reachable():
                self._status = WORKER_STATUS_RUNNING
                _LOGGER.info(
                    "K8s worker '%s' is ready at %s", self._name, self._address
                )
                self._monitor_task = asyncio.create_task(self._monitor())
                await self.async_reload_waiting_entries()
                return
            await asyncio.sleep(interval)

        if not self._stopping:
            _LOGGER.error(
                "K8s worker '%s' did not become ready within %ds",
                self._name,
                timeout,
            )
            self._status = WORKER_STATUS_UNAVAILABLE
            self._schedule_retry()

    async def _monitor(self) -> None:
        """Periodically verify the pod is still running; retry on failure."""
        while not self._stopping:
            await asyncio.sleep(15)
            try:
                is_running = await self._hass.async_add_executor_job(
                    self._check_running
                )
                if not is_running and not self._stopping:
                    _LOGGER.warning(
                        "K8s worker '%s' pod stopped, retrying in %ds",
                        self._name,
                        RETRY_INTERVAL,
                    )
                    self._status = WORKER_STATUS_UNAVAILABLE
                    self._schedule_retry()
                    return
            except Exception:  # noqa: BLE001
                pass

    def _schedule_retry(self) -> None:
        """Schedule a restart attempt after RETRY_INTERVAL seconds."""
        if not self._stopping:
            self._retry_task = asyncio.create_task(self._retry())

    async def _retry(self) -> None:
        """Wait, then restart the worker."""
        await asyncio.sleep(RETRY_INTERVAL)
        if not self._stopping:
            await self.async_start()

    async def async_stop(self) -> None:
        """Stop the K8s worker."""
        self._stopping = True
        for task in (self._retry_task, self._monitor_task):
            if task:
                task.cancel()
        if self._stop_on_shutdown:
            await self._hass.async_add_executor_job(self._stop_sync)
        else:
            _LOGGER.info(
                "K8s worker '%s' stop_on_shutdown=false — leaving pod running",
                self._name,
            )
            self._status = WORKER_STATUS_UNAVAILABLE
