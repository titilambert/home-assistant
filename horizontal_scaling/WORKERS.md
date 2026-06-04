# Home Assistant Horizontal Scaling — Worker Configuration

This document describes how to declare and configure remote workers in Home Assistant.
Workers are declared in `configuration.yaml` under the `horizontal_scaling` key.

---

## Table of Contents

1. [Overview](#overview)
2. [Configuration Structure](#configuration-structure)
3. [Worker Types](#worker-types)
   - [process](#process-worker)
   - [docker](#docker-worker)
   - [remote](#remote-worker)
   - [kubernetes](#kubernetes-worker)
4. [Config Flow Behavior](#config-flow-behavior)
5. [Worker Lifecycle](#worker-lifecycle)
6. [Networking](#networking)
7. [Kubernetes RBAC Requirements](#kubernetes-rbac-requirements)
8. [Full Example](#full-example)

---

## Overview

Workers are long-running processes that host Home Assistant integrations remotely.
Instead of running inside the Core HA process, integrations communicate with Core
via gRPC (bidirectional: states pushed from worker to Core, service calls routed
from Core to worker).

**Key principles:**

- Workers are declared once in `configuration.yaml`
- Integrations are assigned to a worker via the config flow UI
- If no workers are declared, the config flow only offers LOCAL mode (no change)
- Core always retries connection to a worker if it goes down, logging errors

---

## Configuration Structure

```yaml
horizontal_scaling:
  workers:
    - name: "Worker Name"         # unique, human-readable name
      type: process               # process | docker | remote | kubernetes
      port: 50052                 # gRPC port the worker listens on
      max_integrations: 10        # optional, unlimited by default
      # ... type-specific config
```

### Common Fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `name` | ✅ | — | Unique human-readable name shown in the config flow |
| `type` | ✅ | — | Worker type: `process`, `docker`, `remote`, `kubernetes` |
| `port` | ✅ | — | gRPC port the worker listens on |
| `max_integrations` | ❌ | unlimited | Maximum number of integrations this worker can host |

---

## Worker Types

### Process Worker

HA launches a local subprocess at startup. The subprocess runs the generic worker
(`homeassistant.worker.main`) and listens on the specified port.

```yaml
horizontal_scaling:
  workers:
    - name: "Local Worker"
      type: process
      port: 50052
      max_integrations: 10  # optional
```

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `port` | ✅ | — | Port the subprocess listens on |
| `max_integrations` | ❌ | unlimited | Max integrations hosted |

**Behavior:**
- Started automatically when HA starts
- Killed when HA stops
- If the subprocess crashes, Core logs an error and retries periodically
- `worker_address` is automatically deduced as `localhost:{port}`

---

### Docker Worker

HA connects to a Docker daemon and manages a container running the generic worker.

```yaml
horizontal_scaling:
  workers:
    - name: "NAS Worker"
      type: docker
      host: "tcp://192.168.1.50:2375"   # Docker daemon address
      image: "homeassistant/worker:2024.1"
      port: 50052
      max_integrations: 5               # optional
      resources:                        # optional
        cpu: "0.5"                      # equivalent to --cpus="0.5"
        memory: "256m"                  # equivalent to --memory="256m"
        cpu_shares: 512                 # optional, relative CPU priority
```

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `host` | ✅ | — | Docker daemon URL (`tcp://host:port` or `unix:///var/run/docker.sock`) |
| `image` | ✅ | — | Docker image to use for the worker container |
| `port` | ✅ | — | Port exposed from the container to the Docker host |
| `max_integrations` | ❌ | unlimited | Max integrations hosted |
| `resources.cpu` | ❌ | unlimited | CPU limit (Docker `--cpus`) |
| `resources.memory` | ❌ | unlimited | Memory limit (Docker `--memory`) |
| `resources.cpu_shares` | ❌ | 1024 | Relative CPU priority (Docker `--cpu-shares`) |

**Note on Docker image:**
In production, the `image` field should point to the same `homeassistant/home-assistant` image
used by the Core. This ensures all integrations and custom components (HACS) installed in Core
are available in the worker.

Example using the same image as Core:
```yaml
    - name: "NAS Worker"
      type: docker
      host: "tcp://192.168.1.50:2375"
      image: "ghcr.io/home-assistant/home-assistant:stable"
      port: 50053
```

The container is started with `--mode worker` automatically by HA.

For local development, a lightweight `horizontal_scaling/Dockerfile.worker` is available.

**Behavior:**
- At HA startup: any existing container with the same name is stopped and removed,
  then a new container is created and started
- Container name is automatically derived from the worker name:
  `ha_worker_{sanitized_name}` (e.g. `ha_worker_nas_worker`)
- The container is started with `-p {port}:{port}` so the port is accessible
  from the Docker host
- `worker_address` is automatically deduced as `{docker_host_ip}:{port}`
- At HA shutdown: the container is stopped (not removed)
- If the container crashes, Core logs an error and retries periodically

**Docker socket example (local):**
```yaml
    - name: "Local Docker Worker"
      type: docker
      host: "unix:///var/run/docker.sock"
      image: "homeassistant/worker:2024.1"
      port: 50052
```

---

### Remote Worker

HA connects to an already-running worker on a remote machine. HA does not manage
the worker lifecycle — it only establishes a gRPC connection.

```yaml
horizontal_scaling:
  workers:
    - name: "RPi Garage"
      type: remote
      address: "192.168.1.100:50052"    # gRPC address of the running worker
      max_integrations: 3               # optional
```

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `address` | ✅ | — | `host:port` of the already-running worker |
| `max_integrations` | ❌ | unlimited | Max integrations hosted |

**Behavior:**
- HA only establishes a gRPC connection — it does not start or stop the worker
- If the worker is unavailable at HA startup, Core logs an error and retries periodically
- If the worker goes down, Core retries connection periodically and logs errors
- At HA shutdown: nothing — the remote worker keeps running

**Starting the remote worker manually:**
```bash
python -m homeassistant.worker.main \
    --core-address homeassistant.local:50051 \
    --worker-port 50052
```

Or as a systemd service on the remote machine:
```ini
[Unit]
Description=Home Assistant Remote Worker
After=network.target

[Service]
ExecStart=/opt/ha-worker/.venv/bin/python -m homeassistant.worker.main \
    --core-address homeassistant.local:50051 \
    --worker-port 50052
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

---

### Kubernetes Worker

HA creates a Pod and a ClusterIP Service in the specified namespace.
Only **in-cluster** mode is supported by default.

```yaml
horizontal_scaling:
  workers:
    - name: "K8s Worker"
      type: kubernetes
      # Authentication — choose one:
      incluster: true                    # HA runs inside K8s (uses pod service account)
      # kubeconfig: /config/k8s.yaml    # HA runs outside K8s

      namespace: homeassistant
      image: "homeassistant/home-assistant:local"
      port: 50052
      max_integrations: 20              # optional, unlimited by default

      # Optional: path to a custom manifest (Pod + Service).
      # If omitted, HA generates a minimal default manifest.
      # HA will override the following fields regardless of what is in the manifest:
      #   Pod:     metadata.name, metadata.namespace, spec.containers[0].image,
      #            spec.containers[0].env (HA_MODE, HA_WORKER_CORE_ADDRESS,
      #            HA_WORKER_PORT, HA_WORKER_NAME are added/merged)
      #   Service: metadata.name, metadata.namespace, spec.selector, spec.ports[0].port
      # Everything else (labels, annotations, nodeSelector, tolerations, affinity,
      # resources, volumes, etc.) is preserved from the manifest.
      manifest: /config/k8s/worker.yaml

      # Optional: additional K8s manifests applied as-is (NetworkPolicy, PDB, Ingress, etc.)
      extra_manifests:
        - /config/k8s/worker-networkpolicy.yaml
```

**Example manifest** (`/config/k8s/worker.yaml`):
```yaml
apiVersion: v1
kind: Pod
metadata:
  name: ha-worker          # overridden by HA
  labels:
    app: ha-worker
    prometheus.io/scrape: "true"
  annotations:
    prometheus.io/port: "50052"
spec:
  nodeSelector:
    node.ttb.lt/tier: high
  tolerations:
    - key: workload
      operator: Equal
      value: integration
      effect: NoSchedule
  containers:
    - name: worker
      image: placeholder   # overridden by HA with the configured image
      # env is merged by HA: HA_MODE, HA_WORKER_CORE_ADDRESS, HA_WORKER_PORT, HA_WORKER_NAME
      resources:
        requests:
          cpu: "50m"
          memory: "64Mi"
        limits:
          cpu: "200m"
          memory: "256Mi"
---
apiVersion: v1
kind: Service
metadata:
  name: ha-worker          # overridden by HA
  labels:
    app: ha-worker
spec:
  type: ClusterIP
  selector:
    app: ha-worker         # overridden by HA
  ports:
    - port: 50052          # overridden by HA with the configured port
      targetPort: 50052
```

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `incluster` | one of these | — | Use K8s service account (HA runs inside cluster) |
| `kubeconfig` | one of these | — | Path to kubeconfig file (HA runs outside cluster) |
| `namespace` | ✅ | — | K8s namespace for Pod and Service |
| `image` | ✅ | — | Container image |
| `port` | ✅ | — | gRPC port |
| `max_integrations` | ❌ | unlimited | Max integrations |
| `manifest` | ❌ | auto-generated | Path to custom Pod+Service manifest |
| `extra_manifests` | ❌ | none | Additional manifests applied as-is |

**Behavior:**
- At HA startup: any existing Pod/Service with the same name is deleted,
  then a new Pod and ClusterIP Service are created
- Pod name and Service name are derived from the worker name:
  `ha-worker-{sanitized-name}` (e.g. `ha-worker-k8s-worker`)
- `worker_address` is automatically deduced as:
  `ha-worker-{sanitized-name}.{namespace}.svc.cluster.local:{port}`
- At HA shutdown: the Pod and Service are deleted
- If the Pod crashes, Core logs an error and retries periodically
- HA validates its RBAC permissions at startup (see [Kubernetes RBAC Requirements](#kubernetes-rbac-requirements))

---

## Config Flow Behavior

### No workers declared

If `horizontal_scaling.workers` is empty or absent, the config flow does **not**
offer a LOCAL/REMOTE choice. All integrations run locally (default HA behavior,
no change).

### Workers declared

When at least one worker is declared, the config flow adds a **Runtime Mode** step
after the integration-specific connection step:

```
Step 1: Integration config (host, credentials, etc.)   ← unchanged
Step 2: Runtime Mode                                    ← new
  ○ Local  — run in Home Assistant Core (default)
  ● Remote — run in a worker
      Worker: [ NAS Worker ▾ ]   ← dropdown of available workers
```

### Worker unavailable

If the selected worker is not reachable at config flow time, an error is displayed:

```
Cannot connect to worker "NAS Worker" at 192.168.1.50:50052.
Make sure the worker is running and accessible.
```

The integration is not created until a reachable worker is selected (or Local mode
is chosen).

---

## Worker Lifecycle

| Event | process | docker | remote | kubernetes |
|-------|---------|--------|--------|------------|
| HA starts | Launch subprocess | Stop existing + create new container | Connect only | Delete existing + create new Pod+Service |
| HA stops | Kill subprocess | Stop container | Nothing | Delete Pod+Service |
| Worker crashes | Log error + retry | Log error + retry | Log error + retry | Log error + retry |
| Worker at capacity (`max_integrations`) | Config flow shows worker as full | idem | idem | idem |

---

## Networking

| Type | worker_address (auto-deduced) | Notes |
|------|-------------------------------|-------|
| `process` | `localhost:{port}` | Always local |
| `docker` | `{docker_host_ip}:{port}` | Port is exposed via `-p {port}:{port}` |
| `remote` | `{address}` (from config) | User-specified |
| `kubernetes` | `ha-worker-{name}.{namespace}.svc.cluster.local:{port}` | In-cluster ClusterIP Service |

---

## Kubernetes RBAC Requirements

HA needs the following permissions to manage worker Pods and Services.

Apply this manifest in your cluster before enabling Kubernetes workers:

```yaml
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: homeassistant
  namespace: homeassistant
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: homeassistant-worker-manager
  namespace: homeassistant
rules:
  # Pod management
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list", "watch", "create", "delete"]
  # Service management
  - apiGroups: [""]
    resources: ["services"]
    verbs: ["get", "list", "create", "delete"]
  # Permission self-check
  - apiGroups: ["authorization.k8s.io"]
    resources: ["selfsubjectaccessreviews"]
    verbs: ["create"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: homeassistant-worker-manager
  namespace: homeassistant
subjects:
  - kind: ServiceAccount
    name: homeassistant
    namespace: homeassistant
roleRef:
  kind: Role
  name: homeassistant-worker-manager
  apiGroup: rbac.authorization.k8s.io
```

**HA performs a permission check at startup** using `SelfSubjectAccessReview`.
If the required permissions are missing, HA logs a clear error and disables the
Kubernetes worker:

```
ERROR: Kubernetes worker "K8s Worker" disabled — missing RBAC permissions.
Required: pods [get list watch create delete], services [get list create delete]
in namespace "homeassistant".
See horizontal_scaling/WORKERS.md for the required RBAC manifest.
```

---

## Full Example

```yaml
horizontal_scaling:
  workers:
    # Local subprocess worker
    - name: "Local Worker"
      type: process
      port: 50052
      max_integrations: 5

    # Docker worker on a NAS
    - name: "NAS Worker"
      type: docker
      host: "tcp://192.168.1.50:2375"
      image: "homeassistant/worker:2024.1"
      port: 50053
      max_integrations: 10
      resources:
        cpu: "0.5"
        memory: "512m"

    # Already-running worker on a Raspberry Pi
    - name: "RPi Garage"
      type: remote
      address: "192.168.1.100:50054"
      max_integrations: 3

    # In-cluster Kubernetes worker
    - name: "K8s Worker"
      type: kubernetes
      incluster: true
      namespace: "homeassistant"
      image: "homeassistant/home-assistant:local"
      port: 50055
      max_integrations: 20
      manifest: /config/k8s/worker.yaml
```
