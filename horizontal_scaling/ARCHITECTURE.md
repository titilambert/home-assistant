# Home Assistant Runtime Pluggable Architecture

## Vision & Objectives

### Goal

Enable Home Assistant integrations to run in two modes using **the same business logic code**:

- **LOCAL**: In-process execution (current Home Assistant behavior, unchanged)
- **REMOTE**: Separate execution (subprocess, Docker container, or Kubernetes pod)

### Key Principles

1. **Zero regression**: LOCAL mode remains 100% unchanged
2. **Opt-in migration**: Integrations are migrated one by one
3. **Same code**: Business logic is identical in both modes
4. **Isolation**: Remote integrations cannot crash the core
5. **Flexibility**: User chooses the runtime mode per integration

### Use Cases

**Why REMOTE mode?**

- **Isolation**: Integration crashes don't affect Home Assistant core
- **Resource limits**: CPU/memory caps per integration
- **Scalability**: Run resource-intensive integrations on separate machines
- **Security**: Sandbox untrusted integrations
- **Hot reload**: Restart/update integrations without restarting HA core
- **Multi-tenancy**: Multiple HA instances sharing integration services

## Key Principles

1. **Zero Code Duplication**: Integration code (`__init__.py`, `sensor.py`, etc.) is NEVER duplicated between LOCAL and REMOTE modes
2. **Transparent Proxy**: The only difference is the `hass` object - in REMOTE mode it's a gRPC proxy
3. **Same Setup Flow**: Both modes call the same `async_setup_entry()` function
4. **Integration Doesn't Know**: The integration code cannot tell if it's running LOCAL or REMOTE - it's completely unaware
5. **Core Decides Mode**: The Core decides runtime mode BEFORE calling `async_setup_entry()` - integrations don't need factory logic

### Overview

```
┌─────────────────────────────────────────────────────────┐
│              Home Assistant Core                        │
│                                                          │
│  ┌────────────────────────────────────────────────┐    │
│  │  State Machine (in-memory)                     │    │
│  │  Event Bus (in-memory)                         │    │
│  │  Service Registry                              │    │
│  │  Entity Registry                               │    │
│  │  Config                                        │    │
│  └────────────────────────────────────────────────┘    │
│                        ↕                                 │
│  ┌────────────────────────────────────────────────┐    │
│  │         Core gRPC Server (port 50051)          │    │
│  │  - SetState / GetState / SubscribeStates       │    │
│  │  - FireEvent / SubscribeEvents                 │    │
│  │  - RegisterService / CallService               │    │
│  │  - GetConfig / SetData / GetData               │    │
│  │  - RegisterEntity / UpdateEntity               │    │
│  └────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
                          ↕ gRPC (bidirectional)
┌─────────────────────────────────────────────────────────┐
│           Integration Service (remote)                  │
│                                                          │
│  ┌────────────────────────────────────────────────┐    │
│  │    Integration gRPC Server (port 50052+)       │    │
│  │  - Initialize / Start / Stop / Reload          │    │
│  │  - CallService (turn_on, turn_off, ...)       │    │
│  │  - ConfigFlowStep / OptionsFlowStep            │    │
│  └────────────────────────────────────────────────┘    │
│                        ↕                                 │
│  ┌────────────────────────────────────────────────┐    │
│  │        Core gRPC Client (hass proxy)           │    │
│  │  states.async_set() → gRPC SetState()          │    │
│  │  bus.async_fire() → gRPC FireEvent()           │    │
│  │  services.async_call() → gRPC CallService()    │    │
│  └────────────────────────────────────────────────┘    │
│                        ↓                                 │
│  ┌────────────────────────────────────────────────┐    │
│  │     Business Logic (PiHoleIntegration)         │    │
│  │  - async_update()                              │    │
│  │  - async_turn_on() / async_turn_off()          │    │
│  │  Uses hass.* proxy transparently               │    │
│  └────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

### Bidirectional Communication

**Integration → Core (via gRPC client in integration service):**
- `SetState(entity_id, state, attributes)` - Update entity states
- `FireEvent(event_type, data)` - Fire events
- `CallService(domain, service, data)` - Call services on other integrations
- `GetConfig()` - Read Home Assistant configuration
- `SetData() / GetData()` - Store/retrieve integration data
- `RegisterEntity() / UpdateEntity()` - Manage entity registry

**Core → Integration (via gRPC client in core):**
- `Initialize(config)` - Setup the integration
- `Start()` - Start polling/listening
- `Stop()` - Clean shutdown
- `Reload()` - Reconfigure without restart
- `CallService(service, data)` - Execute service (turn_on, turn_off, etc.)
- `ConfigFlowStep(user_input)` - Handle config flow steps

## Runtime Modes

### LOCAL Mode (Unchanged)

Current Home Assistant behavior:

```
components/pi_hole/
├── __init__.py           # Setup code (unchanged)
├── sensor.py             # Sensor entities
├── switch.py             # Switch entities
└── coordinator.py        # DataUpdateCoordinator

Flow:
  Core → import components.pi_hole
      → async_setup_entry(hass, entry)
      → DataUpdateCoordinator polls integration
      → Integration calls hass.states.async_set() directly
```

**Characteristics:**
- In-process execution
- Direct Python calls
- Maximum performance
- No network overhead
- Current default behavior

### REMOTE Mode (New)

Integration runs in a separate process. Two sub-modes:

### REMOTE_DEDICATED Mode

Each integration runs in its own isolated process/container/pod.

```
components/pi_hole/
├── __init__.py                  # Integration code (UNCHANGED)
├── sensor.py                    # Sensor entities (UNCHANGED)
├── switch.py                    # Switch entities (UNCHANGED)
├── coordinator.py               # DataUpdateCoordinator (UNCHANGED)
│
└── remote/
    ├── hass_proxy.py            # HomeAssistantGrpcProxy
    └── main.py                  # Entry point: creates proxy hass, runs setup

Flow:
  Core → ProcessExecutor starts integration
      → Integration creates HomeAssistantGrpcProxy(core_address)
      → Integration imports components.pi_hole
      → Integration calls async_setup_entry(hass_proxy, entry)
      → Setup code runs IDENTICALLY to LOCAL mode
      → hass_proxy.states.async_set() → gRPC → Core
```

**Characteristics:**
- Separate process/container/pod per integration
- Integration code is **identical** to LOCAL
- Only difference: hass object is a gRPC proxy
- Zero code duplication

#### REMOTE_WORKER Mode

Multiple integrations share a Worker process/container/pod for efficiency.

```
Worker Pod (worker-1):
  ├── HomeAssistantGrpcProxy(core_address)  # Shared proxy
  ├── Pi-hole Integration (runs __init__.py with proxy)
  ├── ESPHome Integration (runs __init__.py with proxy)
  └── Nest Integration (runs __init__.py with proxy)

Each integration imports its own code and runs async_setup_entry()
All share the same proxy hass object
```

Flow:
  Core → WorkerExecutor finds worker-1
      → Worker already running with HomeAssistantGrpcProxy
      → Worker imports components.pi_hole
      → Worker calls async_setup_entry(hass_proxy, entry)
      → Setup code runs IDENTICALLY to LOCAL mode
      → Multiple integrations use same proxy hass
```

**Characteristics:**
- Multiple integrations per worker
- All integrations share one HomeAssistantGrpcProxy instance
- Each integration runs its own setup code unchanged
- Resource efficiency (5-10 integrations per worker)
- Reduced isolation (integrations share resources)
- Lower overhead for many integrations

**Worker Implementation:**

The worker is just a Python process that:
1. Creates one `HomeAssistantGrpcProxy`
2. For each integration to load:
   - Imports the integration module
   - Calls `async_setup_entry(hass_proxy, entry)`
3. Keeps running

No special multi-tenant gRPC server needed - each integration just runs normally with the shared proxy.

**Mode Selection:**

```yaml
# UI Configuration
Integration: Pi-hole

# Option 1: LOCAL (default)
Runtime Mode: Local

# Option 2: REMOTE DEDICATED (maximum isolation)
Runtime Mode: Remote Dedicated
Executor: Kubernetes  # or Process, Docker

# Option 3: REMOTE WORKER (resource efficiency)
Runtime Mode: Remote Worker
Worker: worker-1  # Select existing or create new worker
```

## Executors

Executors manage the lifecycle of remote integration services. They are configured in `configuration.yaml` and selected by users per integration.

### Executor Configuration

```yaml
# configuration.yaml
executors:
  # Local subprocess
  - name: process
    type: process
  
  # Docker local
  - name: docker-local
    type: docker
    socket: unix:///var/run/docker.sock
  
  # Docker on NAS
  - name: nas
    type: docker
    host: tcp://192.168.1.50:2375
    tls: false
  
  # Docker on Raspberry Pi with TLS
  - name: pi
    type: docker
    host: tcp://192.168.1.100:2376
    tls: true
    cert: /config/certs/pi-cert.pem
    key: /config/certs/pi-key.pem
    ca: /config/certs/ca.pem
  
  # Kubernetes dedicated pods
  - name: k3s
    type: kubernetes
    kubeconfig: /config/k3s.yaml
    namespace: homeassistant
    pod_template:
      node_selector:
        node.ttb.lt/tier: high
      tolerations:
        - key: workload
          operator: Equal
          value: integration
          effect: NoSchedule
      affinity:
        node_affinity:
          preferred_during_scheduling_ignored_during_execution:
            - weight: 100
              preference:
                match_expressions:
                  - key: kubernetes.io/hostname
                    operator: In
                    values: [draconix]
      priority_class_name: integration-high-priority
      resources:
        requests:
          cpu: 50m
          memory: 64Mi
        limits:
          cpu: 100m
          memory: 128Mi
  
  # Kubernetes worker pods (multi-tenant)
  - name: k3s-worker
    type: kubernetes_worker
    kubeconfig: /config/k3s.yaml
    namespace: homeassistant
    worker_pod_template:
      node_selector:
        node.ttb.lt/tier: mid
      resources:
        requests:
          cpu: 200m
          memory: 512Mi
        limits:
          cpu: 500m
          memory: 1Gi
```

**User Selection:**

When configuring an integration in REMOTE mode, user selects from configured executors:

```
Integration: Pi-hole
Runtime Mode: Remote
Executor: [Dropdown]
  - process (Process - Local)
  - docker-local (Docker - Local Socket)
  - nas (Docker - 192.168.1.50)
  - pi (Docker - 192.168.1.100)
  - k3s (Kubernetes - k3s.home.lan)
  - k3s-worker (Kubernetes Worker - k3s.home.lan)
```

### ProcessExecutor

Runs the integration in a local subprocess.

**Responsibilities:**
- Find a free port
- Launch subprocess: `python -m homeassistant.components.pi_hole.remote.main`
- Wait for gRPC server to be ready
- Return gRPC address: `localhost:PORT`

**Use case:** Simple isolation, development, debugging

**Pros:**
- ✅ Simple setup
- ✅ Easy debugging (logs to stdout)
- ✅ No external dependencies

**Cons:**
- ❌ Limited isolation (same machine)
- ❌ No resource limits (unless using cgroups manually)

### DockerExecutor

Runs the integration in a Docker container (local or remote).

**Configuration:**
- `socket`: Unix socket path for local Docker
- `host`: TCP address for remote Docker
- `tls`: Enable TLS authentication
- `cert`, `key`, `ca`: TLS certificate files

**Responsibilities:**
- Connect to Docker daemon (local or remote)
- Pull/build integration image
- Create container with:
  - Environment variables (CORE_ADDRESS, config)
  - Resource limits (CPU, memory)
  - Network mode (host or bridge)
  - Restart policy
- Return when container is running

**Use case:** Strong isolation, resource limits, local or remote execution

**Networking:**
- **Local Docker**: Container connects to `host.docker.internal:50051` (macOS/Windows) or host IP (Linux)
- **Remote Docker**: Container connects to Core's public/LAN IP (e.g., `192.168.1.10:50051`)

**Multi-host deployment:**

With Docker executors on multiple machines, you can:
- Run heavy integrations on powerful NAS
- Run lightweight integrations on Raspberry Pi
- Distribute load across multiple Docker hosts
- Scale horizontally without Kubernetes

**Example:**
```yaml
executors:
  - name: nas
    type: docker
    host: tcp://192.168.1.50:2375
```

User selects "nas" when configuring integration → Container runs on NAS, connects back to Core via gRPC.

### KubernetesDedicatedExecutor

Runs each integration in its own dedicated Kubernetes Pod.

**Responsibilities:**
- Create Pod manifest with:
  - Container image
  - Environment variables
  - Resource requests/limits (CPU: 50m/100m, Memory: 64Mi/128Mi)
  - Liveness/readiness probes
  - Restart policy: Always
- Create Service (ClusterIP) to expose the Pod
- Wait for Pod to become Ready
- Return gRPC address: `{service-name}.{namespace}.svc.cluster.local:50051`

**Use case:** Production scale, maximum isolation, single-integration workloads

**Pros:**
- ✅ Maximum isolation (one integration per pod)
- ✅ Individual resource limits
- ✅ Automatic orchestration (scheduling, restart)
- ✅ Independent scaling per integration
- ✅ Failure isolation

**Cons:**
- ❌ Requires Kubernetes cluster
- ❌ Higher resource overhead (more pods)
- ❌ More complex setup

### KubernetesWorkerExecutor

Runs multiple integrations in shared Worker Pods for resource efficiency.

**Responsibilities:**
- Find or create a Worker Pod with available capacity
- Connect to the Worker's gRPC server
- Call `Initialize(integration_id, integration_type, config)` on the Worker
- Worker loads and runs the integration alongside others
- Return Worker's gRPC address (shared by multiple integrations)

**Architecture:**

```
Worker Pod (e.g., worker-1):
  ├── Integration 1: Pi-hole
  ├── Integration 2: ESPHome
  ├── Integration 3: Nest
  └── Integration 4: Spotify
  
  Single gRPC server routes to integrations by integration_id
```

**Use case:** Resource optimization, many lightweight integrations, multi-tenancy

**Pros:**
- ✅ Much lower resource overhead (5-10 integrations per pod)
- ✅ Shared Python dependencies
- ✅ Better pod density
- ✅ Cost effective for many integrations
- ✅ Centralized worker management

**Cons:**
- ❌ Reduced isolation (integrations share pod resources)
- ❌ Single failure affects all integrations in worker
- ❌ More complex routing (integration_id required)
- ❌ Requires multi-tenant worker implementation

**Worker Selection Strategies:**

1. **Manual**: User selects which worker to use
2. **Automatic**: Core assigns to least-loaded worker
3. **Affinity**: Group related integrations (e.g., all IoT in worker-1)
4. **Auto-scaling**: Create new workers when existing ones are full

## Integration Migration Pattern

### Step 1: Identify Eligible Integrations

**Can be REMOTE:**
- ✅ Cloud API integrations (Nest, Spotify, Weather, etc.)
- ✅ Local HTTP/REST APIs (Pi-hole, Home Assistant, ESPHome)
- ✅ MQTT-based (zigbee2mqtt, Tasmota)
- ✅ Webhook-based (with adaptation)
- ✅ Polling-based with low frequency (< 1 update/sec)

**Must stay LOCAL:**
- ❌ USB/Serial hardware (Zigbee dongles, Z-Wave, RFLink)
- ❌ Bluetooth/BLE integrations
- ❌ Network discovery (mDNS, SSDP) - unless host networking
- ❌ High-frequency updates (> 10 updates/sec)
- ❌ Direct hardware access

**Estimated coverage: 70-80% of integrations can be REMOTE**

### Step 2: Integration Code Stays Unchanged (CRITICAL)

**The existing integration code is NOT modified.**

```
components/pi_hole/
├── __init__.py           # UNCHANGED - no factory, no runtime mode logic
├── sensor.py             # UNCHANGED
├── switch.py             # UNCHANGED
├── coordinator.py        # UNCHANGED
└── manifest.json         # Add: "supports_remote": true
```

**This code works identically in both LOCAL and REMOTE modes.**

The Core decides the mode BEFORE calling `async_setup_entry()`.

### Step 3: Add Remote Entry Point

Create `remote/` directory with entry point for remote execution:

```
components/pi_hole/
└── remote/
    ├── __init__.py
    └── main.py          # Entry point for remote execution
```

**Remote entry point** (`remote/main.py`):

This file is executed by the Executor (Process/Docker/Kubernetes) and:
1. Creates `HomeAssistantGrpcProxy(core_address)`
2. Imports the integration's `async_setup_entry`
3. Calls it with the proxy hass

The integration code runs identically, just with a proxy hass instead of real hass.

### Step 4: Core Runtime Dispatcher

The Core decides runtime mode in `async_setup_entry()`:

**In Core (`config_entries.py`):**

When setting up a config entry, Core checks runtime mode:

```
if entry.options.get('runtime_mode') == 'local':
    # LOCAL: Import and call directly
    component = await hass.async_import_component(entry.domain)
    await component.async_setup_entry(hass, entry)

elif entry.options.get('runtime_mode') == 'remote':
    # REMOTE: Start executor
    executor_name = entry.options.get('executor')
    executor = get_executor(executor_name)
    await executor.start(entry.domain, entry.entry_id, entry.data)
```

**The integration never sees this logic.** It's all in the Core.

### Step 5: User Configuration

User selects runtime mode and executor in config flow:

```yaml
Integration: Pi-hole
Runtime Mode: Remote
Executor: nas  # from configured executors
```

Configuration is stored in `entry.options`:
- `runtime_mode`: 'local' or 'remote'
- `executor`: executor name (if remote)

## Code Structure in Home Assistant Repository

All code resides within the `homeassistant/` directory from day one (including POC).

```
homeassistant/
├── components/
│   └── pi_hole/
│       ├── __init__.py          # Existing code (UNCHANGED)
│       ├── sensor.py            # Existing code (UNCHANGED)
│       ├── switch.py            # Existing code (UNCHANGED)
│       ├── coordinator.py       # Existing code (UNCHANGED)
│       ├── manifest.json        # Add: "supports_remote": true
│       └── remote/              # NEW - Remote mode entry point
│           ├── __init__.py
│           └── main.py          # Entry point for remote execution
│
├── grpc/                        # NEW - Core gRPC Server
│   ├── __init__.py
│   ├── server.py                # gRPC server startup
│   ├── protos/
│   │   ├── core.proto           # Core API definition
│   │   └── (generated files)
│   └── services/
│       ├── state_service.py     # SetState, GetState, etc.
│       ├── event_service.py     # FireEvent, SubscribeEvents
│       ├── service_service.py   # RegisterService, CallService
│       ├── config_service.py    # GetConfig
│       └── ...
│
├── executors/                   # NEW - Executor implementations
│   ├── __init__.py
│   ├── base.py                  # ExecutorBase interface
│   ├── factory.py               # ExecutorFactory
│   ├── process.py               # ProcessExecutor
│   ├── docker.py                # DockerExecutor
│   └── kubernetes.py            # KubernetesExecutor (Dedicated & Worker)
│
└── helpers/
    └── remote_hass.py           # NEW - HomeAssistantGrpcProxy (shared by all integrations)
```

**Key points:**

1. **No separate POC directory** - Code is in the proper location from the start
2. **Integration code unchanged** - Existing files remain untouched
3. **Shared proxy** - `HomeAssistantGrpcProxy` in `helpers/` is reused by all remote integrations
4. **Core gRPC in `grpc/`** - Centralized gRPC server code
5. **Executors in `executors/`** - Executor implementations and factory

The Core always starts a gRPC server on startup (port 50051, localhost by default).

### Services Exposed

**State Machine:**
- `SetState(entity_id, state, attributes)` → Update entity state
- `GetState(entity_id)` → Retrieve entity state
- `SubscribeStates(entity_pattern)` → Stream state changes

**Event Bus:**
- `FireEvent(event_type, data)` → Fire an event
- `SubscribeEvents(event_type_pattern)` → Stream events

**Services:**
- `RegisterService(domain, service, schema)` → Register a service handler
- `CallService(domain, service, data)` → Call a service

**Config:**
- `GetConfig()` → Read HA configuration (location, timezone, etc.)

**Data Storage:**
- `SetData(domain, key, value)` → Store integration data
- `GetData(domain, key)` → Retrieve integration data

**Entity Registry:**
- `RegisterEntity(entity_id, metadata)` → Register entity in registry
- `UpdateEntity(entity_id, updates)` → Update entity metadata
- `RemoveEntity(entity_id)` → Unregister entity

**Device Registry:**
- `RegisterDevice(device_id, metadata)` → Register device
- `UpdateDevice(device_id, updates)` → Update device metadata

**Lifecycle:**
- `NotifyShutdown()` → Broadcast to all remote integrations
- `NotifyReload(domain)` → Request integration reload

### Implementation

Located in `homeassistant/grpc/`:
- `server.py` - Main gRPC server
- `services/state_service.py` - State machine service
- `services/event_service.py` - Event bus service
- `services/service_service.py` - Service registry service
- `services/config_service.py` - Config service
- etc.

Started in `homeassistant/__main__.py`:

```python
async def async_start(hass):
    # ... existing setup ...
    
    # Start gRPC server
    from homeassistant.grpc import start_grpc_server
    grpc_server = await start_grpc_server(hass, port=50051)
    hass.grpc_server = grpc_server
    
    # ... rest of startup ...
```

## Technical Choices

### Why gRPC?

**Pros:**
- ✅ **Strong schema**: Protobuf defines clear contracts
- ✅ **Code generation**: Type-safe stubs, autocompletion
- ✅ **Bidirectional streaming**: Native support for Integration → Core and Core → Integration
- ✅ **HTTP/2**: Efficient multiplexing, connection reuse
- ✅ **Performance**: Binary protocol, faster than JSON
- ✅ **Ecosystem**: Mature tooling (grpcurl, grpc-gateway, etc.)
- ✅ **Standard**: Industry-proven (Google, Netflix, etc.)

**Cons:**
- ⚠️ **Binary protocol**: Harder to debug (mitigated by grpcurl)
- ⚠️ **Proto compilation**: Extra build step (automated)
- ⚠️ **Learning curve**: Steeper than REST (but well-documented)

**Alternatives considered:**
- WebSocket + JSON: Simpler but no schema, less performant
- NATS: Great for scale but requires broker, overkill for POC
- HTTP/2 + SSE: Good for unidirectional, not optimal for bidirectional
- Apache Thrift: Similar to gRPC but smaller ecosystem

**Verdict:** gRPC is the best fit for this use case.

## Roadmap

### Phase 0: POC (Proof of Concept)

**Goal:** Prove the concept with Pi-hole integration

**Scope:**
- Core gRPC Server (SetState only)
- Integration gRPC Server (Initialize, Start, CallService)
- PiHoleIntegration business logic
- Manual launch (no executors)

**Duration:** ~8 hours

**Success criteria:** Pi-hole runs remotely, states sync, commands work

### Phase 1: ProcessExecutor + Basic Features

**Goal:** Automate subprocess launch, add Events/Services

**Scope:**
- ProcessExecutor implementation
- Core gRPC Server: Add FireEvent, CallService
- Integration runtime factory in `__init__.py`
- Config flow for runtime mode selection

**Duration:** ~2 days

**Success criteria:** User can configure Pi-hole in REMOTE mode via UI

### Phase 2: DockerExecutor

**Goal:** Container isolation

**Scope:**
- DockerExecutor implementation
- Dockerfile for pi_hole integration
- Resource limits configuration
- Image build/push automation

**Duration:** ~2 days

**Success criteria:** Pi-hole runs in Docker container with resource limits

### Phase 3: KubernetesDedicatedExecutor

**Goal:** Production-ready orchestration with maximum isolation

**Scope:**
- KubernetesDedicatedExecutor implementation
- Pod/Service manifest templates
- Health checks and probes
- Helm chart (optional)

**Duration:** ~3 days

**Success criteria:** Pi-hole runs as K8s Pod, auto-restarts on failure

### Phase 4: KubernetesWorkerExecutor

**Goal:** Resource-efficient multi-tenant workers

**Scope:**
- Worker Pod implementation (multi-tenant gRPC server)
- KubernetesWorkerExecutor implementation
- Worker discovery and selection logic
- Worker auto-scaling (optional)
- Worker management UI

**Duration:** ~5 days

**Success criteria:** 
- Multiple integrations run in single Worker Pod
- Core can route calls by integration_id
- Worker scales when capacity reached

### Phase 5: Complete Core API

**Goal:** Support all hass.* APIs

**Scope:**
- Entity/Device Registry gRPC services
- Config gRPC service
- Translation gRPC service
- WebSocket handler registration
- Logger streaming (optional)

**Duration:** ~5 days

**Success criteria:** Complex integrations can be migrated

### Phase 6: Generalization + Tooling

**Goal:** Make migration easy for all integrations

**Scope:**
- Base classes for easy migration
- CLI tool to scaffold remote-ready integration
- Migration guide documentation
- Automated tests framework
- Observability (metrics, tracing)

**Duration:** ~1 week

**Success criteria:** Any developer can migrate an integration in < 1 day

## Executor Comparison Matrix

| Executor | Isolation | Resource Overhead | Use Case | Complexity |
|----------|-----------|-------------------|----------|------------|
| **ProcessExecutor** | Medium | Low (subprocess) | Development, debugging | Low |
| **DockerExecutor** | High | Medium (container) | Production, isolation | Medium |
| **KubernetesDedicatedExecutor** | Maximum | High (pod per integration) | Mission-critical, full isolation | High |
| **KubernetesWorkerExecutor** | Low | Very Low (shared pod) | Many integrations, cost optimization | High |

**Recommended strategies:**

- **Small deployment (1-5 integrations)**: ProcessExecutor or DockerExecutor
- **Medium deployment (5-20 integrations)**: KubernetesDedicatedExecutor
- **Large deployment (20+ integrations)**: KubernetesWorkerExecutor
- **Hybrid**: Critical integrations in Dedicated, others in Worker

## Integration Compatibility Matrix

| Integration Type | Remote Support | Notes |
|-----------------|----------------|-------|
| Cloud APIs (REST/HTTP) | ✅ Full | Nest, Spotify, Weather, etc. |
| Local HTTP/REST APIs | ✅ Full | Pi-hole, ESPHome, Shelly |
| MQTT | ✅ Full | zigbee2mqtt, Tasmota |
| Webhooks | ✅ With adaptation | Need to expose endpoint |
| Polling (< 1 Hz) | ✅ Full | Most sensors |
| USB/Serial | ❌ LOCAL only | ZHA, Z-Wave, RFLink |
| Bluetooth/BLE | ❌ LOCAL only | Trackers, locks |
| mDNS/SSDP discovery | ⚠️ With host network | Chromecast, Sonos |
| High frequency (> 10 Hz) | ❌ LOCAL only | Cameras, audio |
| Complex state graphs | ⚠️ Requires design | Zigbee coordinator |

**Legend:**
- ✅ Full support
- ⚠️ Possible with limitations
- ❌ Not supported (must stay LOCAL)

## Benefits

### For Users

- **Stability**: Integration crashes don't affect HA core
- **Performance**: Offload heavy integrations to separate machines
- **Security**: Isolate untrusted integrations
- **Flexibility**: Mix LOCAL and REMOTE as needed

### For Developers

- **Easier testing**: Test integrations in isolation
- **Hot reload**: Faster development iteration
- **Clear separation**: Business logic vs HA coupling
- **Reusability**: Same code for LOCAL and REMOTE

### For Operations

- **Resource management**: Set CPU/RAM limits per integration
- **Scaling**: Run multiple instances of same integration
- **Monitoring**: Per-integration metrics
- **Deployment**: Update integrations independently

## Security Considerations

### POC (Phase 0)
- gRPC on localhost only
- No authentication
- Insecure channels

### Production (Future)
- **mTLS**: Mutual TLS for gRPC channels
- **API tokens**: Authentication for service calls
- **Network policies**: Kubernetes NetworkPolicy for pod-to-pod
- **Secrets management**: Vault or K8s secrets for credentials
- **RBAC**: Role-based access for Core API methods

## Performance Characteristics

### Overhead

**LOCAL (baseline):**
- Direct Python call: ~0.001ms
- Zero serialization
- Zero network

**REMOTE (Process):**
- gRPC call (localhost): ~1-2ms
- Protobuf serialization: ~0.5ms
- Total overhead: ~2-3ms per call

**REMOTE (Docker):**
- gRPC call (Docker network): ~2-5ms
- Same serialization
- Total overhead: ~3-6ms per call

**REMOTE (Kubernetes):**
- gRPC call (K8s network): ~5-10ms
- Same serialization
- Total overhead: ~6-11ms per call

**Verdict:** Overhead is acceptable for most integrations (< 1% of typical polling interval of 30s)

### Throughput

- gRPC can handle 10,000+ RPC/sec on modern hardware
- For most integrations (1 update every 30s), this is not a bottleneck

### Memory

- Core gRPC server: ~10-20 MB
- Integration gRPC server: ~5-10 MB per integration
- Docker container overhead: ~50-100 MB

## Future Extensions

### Auto-scaling (Kubernetes)
- Horizontal Pod Autoscaler based on CPU/memory
- Scale integrations based on load
- Multiple replicas for high availability

### Multi-tenancy
- Multiple HA instances share integration services
- Cost savings by pooling resources
- Strong tenant isolation

### Observability
- Prometheus metrics per integration
- Distributed tracing (OpenTelemetry)
- Grafana dashboards

### Edge Computing
- Run integrations on edge devices (Raspberry Pi)
- Low-latency local processing
- Centralized Home Assistant core

### WebAssembly Runtime
- Compile integrations to WASM
- Ultra-lightweight isolation
- Cross-platform portability
