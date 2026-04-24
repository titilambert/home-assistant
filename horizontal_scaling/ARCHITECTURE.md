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

**Worker Config:**
- `GetEntry(entry_id)` → `{domain, config, options}` — Worker requests its config from the Core

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

### Phase 0: POC (Proof of Concept) ✅ COMPLETED (~2025-06)

**Goal:** Prove the concept with Pi-hole integration

**Scope:**
- Core gRPC Server (SetState only)
- Integration gRPC Server (Initialize, Start, CallService)
- PiHoleIntegration business logic
- Manual launch (no executors)

**Duration:** ~8 hours

**Success criteria:** Pi-hole runs remotely, states sync, commands work ✅

### Phase 1: Generic Worker + ProcessExecutor

**Goal:** Automate subprocess launch with a generic worker, add Events/Services

**Scope:**
- Generic worker (`homeassistant/worker/`) — universal runtime for N integrations without modifying their code
- **Generic worker supports multiple integrations in a single process**
- **Worker receives a list of entry_ids and loads each integration dynamically**
- `GetEntry` gRPC to retrieve an integration's config from the Core
- Missing shims: DeviceRegistry, EntityRegistry, IssueRegistry, Dispatcher, Timers, Storage
- RuntimeFactory in integrations (LOCAL vs REMOTE)
- Improved ProcessExecutor (launches the generic worker)
- Removal of `pi_hole/remote/main.py` (replaced by the generic worker)
- Config flow for runtime mode selection
- Config flow LOCAL/REMOTE mode selection in the UI
- Integration setup UI: choose executor (process, docker, kubernetes)

**Duration:** ~3 days

**Success criteria:** Generic worker loads Pi-hole (and any compatible integration) without modifying the integration code

### Phase 2: DockerExecutor

**Goal:** Container isolation

**Scope:**
- DockerExecutor implementation
- Dockerfile for pi_hole integration
- Resource limits configuration
- Image build/push automation

**Duration:** ~2 days

**Success criteria:** Pi-hole runs in Docker container with resource limits

### Phase 3: KubernetesExecutor

**Goal:** Deploy workers on Kubernetes with pool management

**Scope:**
- KubernetesExecutor implementation (replaces KubernetesDedicatedExecutor + KubernetesWorkerExecutor)
- Worker Pod pool management (discovery, capacity tracking)
- Routing logic: Core assigns entry_ids to the least-loaded worker pod
- Worker auto-scaling (HPA based on number of integrations)
- Configuration: max_integrations_per_worker (default: unlimited, set to 1 for dedicated mode)
- Pod/Service manifest templates
- Health checks and probes

**Note:** A "dedicated" pod per integration is just a special case: set max_integrations_per_worker: 1 in the executor config. No separate executor needed.

**Duration:** ~4 days

**Success criteria:**
- Multiple integrations run in worker pods on Kubernetes
- Core automatically assigns integrations to the least-loaded pod
- Pods auto-scale when capacity is reached
- max_integrations_per_worker=1 gives dedicated pod behavior

### Phase 4: UI Dashboard Workers

**Goal:** Give users visibility and control over remote workers from the HA UI

**Scope:**
- Workers dashboard panel (list of active workers, status, health)
- Per-worker detail: integrations running, uptime, gRPC latency, last seen
- Worker actions: restart, stop, reassign integration to different worker
- Integration status: LOCAL vs REMOTE badge in the integrations list
- Notifications: worker disconnected, integration crashed in worker
- Config entry UI: show which worker is running this integration

**Duration:** ~3 days

**Success criteria:**
- User can see all active workers and their integrations in the HA UI
- User can restart a worker from the UI
- Integrations list shows LOCAL/REMOTE status for each integration
- User is notified when a worker goes offline

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

### Phase 7: Custom Components Support (HACS + GitHub)

**Goal:** Support custom integrations in workers

**Scope:**
- ComponentResolver implementation (`homeassistant/worker/resolver.py`)
- GetEntry proto: add `source` field
- HACS index resolver (hacs/default → GitHub URL)
- GitHub downloader (zip download + extraction)
- Local cache management
- Version pinning (@tag, @commit, @branch)

**Duration:** ~2 days

**Success criteria:** 
- A HACS integration runs in a worker without being installed on the Core machine
- Version is pinned and reproducible
- Cache prevents re-download on worker restart

## Executor Comparison Matrix

| Executor | Isolation | Resource Overhead | Use Case | Complexity |
|----------|-----------|-------------------|----------|------------|
| **ProcessExecutor** | Medium | Low (subprocess) | Development, debugging | Low |
| **DockerExecutor** | High | Medium (container) | Production, isolation | Medium |
| **KubernetesExecutor** | High to Maximum | Low to High (configurable) | Production at scale, max_integrations_per_worker configurable | High |

> KubernetesExecutor covers both "dedicated" (max_integrations_per_worker=1) and "worker pool" (max_integrations_per_worker=N) deployment models.

**Recommended strategies:**

- **Small deployment (1-5 integrations)**: ProcessExecutor or DockerExecutor
- **Production deployment**: KubernetesExecutor with max_integrations_per_worker=N
- **Maximum isolation**: KubernetesExecutor with max_integrations_per_worker=1

## Generic Worker

The generic worker is a **universal HA runtime** capable of loading N integrations without modifying their source code.

### Architecture

```
homeassistant/worker/
├── main.py          # Generic entry point
├── runtime.py       # WorkerRuntime — orchestrates integrations
└── loader.py        # IntegrationLoader — dynamically loads integrations
```

### Startup Flow

1. Core launches the worker with `--core-address localhost:50051 --entry-id <id1> --entry-id <id2>`
2. Worker connects to Core
3. For each `entry_id`, worker calls `GetEntry(entry_id)` → receives `{domain, config, options}`
4. Worker creates a shared `HomeAssistantGrpcProxy`
5. Worker calls `async_setup_entry(hass_proxy, entry)` for each integration
6. Worker starts its `WorkerGrpcServer` and registers via `RegisterWorker`

### Multiple Integrations in a Single Worker

- A single shared `HomeAssistantGrpcProxy` across all integrations in the worker
- `entry_id` is used to route `SetState` and `CallService` to the correct integration
- Each integration has its own `_MinimalConfigEntry`

### Advantages

- **Zero modification** of existing integrations: integration code is unchanged
- **Efficiency**: multiple integrations share a single process and a single gRPC connection
- **Universality**: any domain loadable by HA can run in the worker
- **Compatibility**: `HomeAssistantGrpcProxy` faithfully simulates the `hass` API

## Component Resolver

### Objective
The generic worker must be able to load any integration, whether built-in to HA or custom (HACS, GitHub).

### Integration Source
The Core sends the `source` in `GetEntryResponse`:

```protobuf
message GetEntryResponse {
  string domain = 1;
  map<string, string> config = 2;
  map<string, string> options = 3;
  string source = 4;  // "builtin", "github:user/repo@v1.2.3", "hacs:domain"
}
```

### Supported Source Types

| Source | Format | Example |
|--------|---------|---------|
| Built-in HA | `builtin` | `builtin` |
| GitHub direct | `github:user/repo@ref` | `github:custom-components/pi_hole@v1.2.3` |
| GitHub (branch) | `github:user/repo@branch` | `github:user/repo@main` |
| HACS | `hacs:domain` | `hacs:pi_hole` |

### HACS → GitHub Resolution
HACS maintains a public index on GitHub (`hacs/default`). The resolver consults this index to resolve `hacs:domain` → corresponding GitHub URL, then proceeds as for a GitHub source.

```
ComponentResolver.resolve(domain, source)
  ├─> "builtin"      → import homeassistant.components.{domain}
  ├─> "github:..."   → downloads the zip, extracts into local sys.path
  └─> "hacs:..."     → resolves via hacs/default index → same as github
```

### Implementation in the Worker

```
homeassistant/worker/
├── main.py
├── runtime.py
├── loader.py
└── resolver.py      # ComponentResolver — nouveau
```

`ComponentResolver`:
- Local cache in the worker's `config_dir` to avoid re-downloads
- Verifies hash/tag for reproducibility
- Offline support if already cached

### Who Knows the Source?
The Core is the source of truth — it knows where the integration comes from (installed via HACS, manually, or built-in). It sends it to the worker via `GetEntry`.

## Integration Compatibility Matrix

| Integration Type | Remote Support | Notes |
|-----------------|----------------|-------|
| Cloud APIs (REST/HTTP) | ✅ Full | Nest, Spotify, Weather, etc. |
| Local HTTP/REST APIs | ✅ Full | Pi-hole, ESPHome, Shelly |
| MQTT | ✅ Full | zigbee2mqtt, Tasmota |
| Webhooks | ✅ With adaptation | Need to expose endpoint |
| Polling (< 1 Hz) | ✅ Full | Most sensors |
| USB/Serial | ✅/❌ Hardware-dependent | ✅ if hardware available on the worker machine, ❌ otherwise |
| Bluetooth/BLE | ✅/❌ Hardware-dependent | ✅ if hardware available on the worker machine (local BLE dongle), ❌ otherwise |
| mDNS/SSDP discovery | ⚠️ With host network | Chromecast, Sonos |
| Interactive config flows | ✅ Core only | ✅ Stay in Core (unchanged) |
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
