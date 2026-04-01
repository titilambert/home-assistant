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

## Global Architecture

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

#### REMOTE_DEDICATED Mode

Each integration runs in its own isolated process/container/pod.

```
components/pi_hole/
├── __init__.py                  # Factory (chooses LOCAL vs REMOTE)
├── integration.py               # Business logic (shared)
├── runtime_local.py             # LOCAL mode wrapper
├── runtime_remote.py            # REMOTE mode client
└── remote/
    ├── grpc_server.py           # Integration gRPC server
    ├── hass_proxy.py            # Proxy hass.* via gRPC
    └── main.py                  # Standalone entry point

Flow:
  Core → factory chooses REMOTE
      → Executor starts integration service (Process/Docker/K8s)
      → Core connects via gRPC client
      → Core.CallService() → gRPC → Integration.CallService()
      → Integration.async_update() → hass_proxy.SetState() → gRPC → Core
```

**Characteristics:**
- Separate process/container/pod per integration
- gRPC communication
- Maximum isolation
- Individual resource limits
- Hot reload capability

#### REMOTE_WORKER Mode

Multiple integrations share a Worker process/container/pod for efficiency.

```
Worker Pod (worker-1):
  ├── Pi-hole Integration (integration_id: abc-123)
  ├── ESPHome Integration (integration_id: def-456)
  ├── Nest Integration (integration_id: ghi-789)
  └── Multi-tenant gRPC Server
      Routes calls by integration_id

Flow:
  Core → factory chooses REMOTE_WORKER
      → WorkerExecutor finds available worker
      → WorkerExecutor.Initialize(integration_id, type, config) via gRPC
      → Worker loads integration class and starts it
      → Core.CallService(integration_id, ...) → gRPC → Worker routes to integration
      → Integration.async_update() → hass_proxy.SetState() → gRPC → Core
```

**Characteristics:**
- Multiple integrations per worker
- Shared gRPC server (routes by integration_id)
- Resource efficiency (5-10 integrations per worker)
- Reduced isolation (integrations share resources)
- Lower overhead for many integrations

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

Executors manage the lifecycle of remote integration services.

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

Runs the integration in a Docker container.

**Responsibilities:**
- Pull/build image: `homeassistant/pihole-integration:latest`
- Create container with:
  - Environment variables (config)
  - Port mapping
  - Resource limits (CPU: 0.5 core, Memory: 128Mi)
  - Network mode (host or bridge)
  - Restart policy
- Wait for container health check
- Return gRPC address: `localhost:PORT`

**Use case:** Strong isolation, resource limits, portability

**Pros:**
- ✅ Complete isolation (namespaces, cgroups)
- ✅ Strict resource limits
- ✅ Portable (Docker image)
- ✅ Easy deployment

**Cons:**
- ❌ Requires Docker
- ❌ Slightly higher overhead

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

### Step 2: Extract Business Logic

Create `integration.py` with pure business logic:

```python
# components/pi_hole/integration.py

class PiHoleIntegration:
    """Pure business logic, no Home Assistant dependencies"""
    
    def __init__(self, host: str, api_key: str):
        self.host = host
        self.api_key = api_key
        self.api = None
        self.entities_state = {}
    
    async def async_initialize(self):
        """Initialize connection to Pi-hole"""
        self.api = Hole(self.host, self.api_key)
        await self.api.get_data()
    
    async def async_update(self) -> dict:
        """Poll Pi-hole and return entity states"""
        data = await self.api.get_data()
        
        self.entities_state = {
            'sensor.ads_blocked': {
                'state': str(data['ads_blocked_today']),
                'attributes': {'domains_blocked': data['domains_being_blocked']}
            },
            'switch.pihole': {
                'state': 'on' if data['status'] == 'enabled' else 'off',
                'attributes': {}
            }
        }
        
        return self.entities_state
    
    async def async_turn_on(self):
        await self.api.enable()
        await self.async_update()
    
    async def async_turn_off(self):
        await self.api.disable()
        await self.async_update()
```

**Key characteristic: Stateless, no `hass` dependency**

### Step 3: Create Runtime Wrappers

**LOCAL wrapper** (`runtime_local.py`):

```python
class LocalRuntime:
    """Uses PiHoleIntegration in-process"""
    
    def __init__(self, hass, config):
        self.hass = hass
        self.integration = PiHoleIntegration(config['host'], config['api_key'])
        self.coordinator = DataUpdateCoordinator(
            hass,
            update_method=self._async_update
        )
    
    async def _async_update(self):
        states = await self.integration.async_update()
        # Direct call to hass
        for entity_id, state_data in states.items():
            self.hass.states.async_set(entity_id, state_data['state'], state_data['attributes'])
```

**REMOTE client** (`runtime_remote.py`):

```python
class RemoteRuntime:
    """Communicates with integration service via gRPC"""
    
    def __init__(self, hass, config, executor):
        self.hass = hass
        self.executor = executor  # ProcessExecutor / DockerExecutor / KubernetesExecutor
        self.grpc_stub = None
    
    async def initialize(self):
        # Start the integration service
        grpc_address = await self.executor.start(config)
        
        # Connect gRPC client
        channel = grpc.aio.insecure_channel(grpc_address)
        self.grpc_stub = IntegrationServiceStub(channel)
        
        # Initialize remote integration
        await self.grpc_stub.Initialize(config)
        await self.grpc_stub.Start()
        
        # Subscribe to state updates
        asyncio.create_task(self._stream_states())
    
    async def _stream_states(self):
        async for update in self.grpc_stub.StreamStateUpdates():
            for entity_id, state_data in update.states.items():
                self.hass.states.async_set(entity_id, state_data.state, state_data.attributes)
```

### Step 4: Create Remote Service

**Integration gRPC server** (`remote/grpc_server.py`):

```python
class IntegrationGrpcServer:
    def __init__(self):
        self.integration = None
        self.core_client = None  # gRPC client to Core
    
    async def Initialize(self, request, context):
        self.integration = PiHoleIntegration(request.config['host'], request.config['api_key'])
        await self.integration.async_initialize()
        
        # Connect to Core
        self.core_client = CoreGrpcClient('localhost:50051')
        
        return InitResponse(success=True, entity_ids=['sensor.ads_blocked', 'switch.pihole'])
    
    async def Start(self, request, context):
        # Start polling loop
        asyncio.create_task(self._update_loop())
        return StartResponse(success=True)
    
    async def _update_loop(self):
        while True:
            states = await self.integration.async_update()
            # Send states to Core via gRPC
            for entity_id, state_data in states.items():
                await self.core_client.set_state(entity_id, state_data['state'], state_data['attributes'])
            await asyncio.sleep(30)
    
    async def CallService(self, request, context):
        if request.service == 'turn_on':
            await self.integration.async_turn_on()
        elif request.service == 'turn_off':
            await self.integration.async_turn_off()
        return CallServiceResponse(success=True)
```

### Step 5: Update Manifest

```json
{
  "domain": "pi_hole",
  "name": "Pi-hole",
  "version": "2.0.0",
  "supports_remote": true,
  "dependencies": ["http"],
  "requirements": ["hole==0.7.0"]
}
```

### Step 6: User Configuration

```yaml
# Via UI config flow options:
runtime_mode: remote  # or 'local'
remote_executor: process  # or 'docker', 'kubernetes'
```

## Core gRPC Server (Complete)

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
