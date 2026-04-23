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
