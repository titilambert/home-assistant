"""Constants for the horizontal_scaling component."""

DOMAIN = "horizontal_scaling"

# Configuration keys
CONF_WORKERS = "workers"
CONF_WORKER_NAME = "name"
CONF_WORKER_TYPE = "type"
CONF_WORKER_PORT = "port"
CONF_WORKER_MAX_INTEGRATIONS = "max_integrations"
CONF_WORKER_ADDRESS = "address"
CONF_WORKER_IMAGE = "image"
CONF_WORKER_HOST = "host"
CONF_WORKER_NAMESPACE = "namespace"
CONF_WORKER_POD_SPEC = "pod_spec"
CONF_WORKER_RESOURCES = "resources"
CONF_WORKER_RESOURCES_CPU = "cpu"
CONF_WORKER_RESOURCES_MEMORY = "memory"
CONF_WORKER_RESOURCES_CPU_SHARES = "cpu_shares"
CONF_WORKER_STOP_ON_SHUTDOWN = "stop_on_shutdown"

# Worker types
WORKER_TYPE_PROCESS = "process"
WORKER_TYPE_DOCKER = "docker"
WORKER_TYPE_REMOTE = "remote"
WORKER_TYPE_KUBERNETES = "kubernetes"

# hass.data keys
DATA_WORKER_REGISTRY = "horizontal_scaling_worker_registry"

# Worker statuses
WORKER_STATUS_STARTING = "starting"
WORKER_STATUS_RUNNING = "running"
WORKER_STATUS_UNAVAILABLE = "unavailable"
WORKER_STATUS_NOT_IMPLEMENTED = "not_implemented"
