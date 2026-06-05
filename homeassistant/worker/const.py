"""Constants for the Home Assistant worker."""

# Worker types
WORKER_TYPE_PROCESS = "process"
WORKER_TYPE_DOCKER = "docker"
WORKER_TYPE_REMOTE = "remote"
WORKER_TYPE_KUBERNETES = "kubernetes"

# Worker statuses
WORKER_STATUS_STARTING = "starting"
WORKER_STATUS_RUNNING = "running"
WORKER_STATUS_UNAVAILABLE = "unavailable"
WORKER_STATUS_NOT_IMPLEMENTED = "not_implemented"

# hass.data key for the worker registry
DATA_WORKER_REGISTRY = "worker_registry"
