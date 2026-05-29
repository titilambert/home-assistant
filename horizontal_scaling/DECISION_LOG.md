# Decision Log

This document records the key architectural and technical decisions made for the Home Assistant Runtime Pluggable project, along with the reasoning behind each choice.

## Table of Contents

1. [Communication Protocol](#communication-protocol)
2. [Bidirectional Architecture](#bidirectional-architecture)
3. [Core gRPC Server Always Running](#core-grpc-server-always-running)
4. [LOCAL Mode Unchanged](#local-mode-unchanged)
5. [Opt-in Migration](#opt-in-migration)
6. [No Changes to Integration Query Loop](#no-changes-to-integration-query-loop)
7. [Executor Abstraction](#executor-abstraction)
8. [Service Call Routing](#service-call-routing)
9. [POC Scope](#poc-scope)
10. [Generic Worker vs Specific Entry Points](#generic-worker-vs-specific-entry-points)
11. [Configuration: Pull (Worker requests) vs Push (Core sends)](#configuration-pull-worker-requests-vs-push-core-sends)
12. [Strategy for HA Registries (Device, Entity, Issue, Area)](#strategy-for-ha-registries-device-entity-issue-area)
13. [Local Hardware in Remote Workers](#local-hardware-in-remote-workers)
14. [Config Flows: Execution in Core Only](#config-flows-execution-in-core-only)
15. [Support for Custom Integrations — ComponentResolver](#support-for-custom-integrations--componentresolver)
16. [Multiple Integrations from Phase 1](#multiple-integrations-from-phase-1)
17. [KubernetesDedicatedExecutor Removed — Merged into KubernetesExecutor](#kubernetesdedicatedexecutor-removed--merged-into-kubernetesexecutor)
18. [Worker Configuration as a Dedicated Phase](#worker-configuration-as-a-dedicated-phase)
19. [Summary](#summary)

---

## Communication Protocol

**Decision:** Use gRPC with Protocol Buffers for Core ↔ Integration communication

**Date:** 2024

**Context:**
We need a communication protocol for bidirectional communication between Home Assistant Core and remote integrations. The protocol must support:
- Request/response (RPC)
- Streaming (for state updates and events)
- Strong typing
- Good performance
- Multiple transport layers (localhost, Docker network, Kubernetes)

**Alternatives Considered:**

1. **WebSocket + JSON**
   - Pros: Simple, human-readable, easy debugging
   - Cons: No schema enforcement, manual serialization, slower than binary

2. **HTTP/2 + Server-Sent Events (SSE)**
   - Pros: Standard web tech, good for unidirectional streaming
   - Cons: SSE is unidirectional only, not optimal for bidirectional

3. **NATS (Message Queue)**
   - Pros: Great for pub/sub, natural decoupling, built-in load balancing
   - Cons: Requires separate broker, overkill for POC, additional operational complexity

4. **ZeroMQ**
   - Pros: Very fast, multiple patterns, no broker needed
   - Cons: Too low-level, need to build protocol layer on top, no schema

5. **Apache Thrift**
   - Pros: Similar to gRPC, multi-language support
   - Cons: Smaller ecosystem, less momentum than gRPC

6. **gRPC** ✅
   - Pros: Strong schema (Protobuf), code generation, bidirectional streaming, HTTP/2, excellent performance, huge ecosystem
   - Cons: Binary protocol (harder to debug), proto compilation step, steeper learning curve

**Decision Rationale:**

gRPC is the best fit because:

1. **Strong Schema**: Protocol Buffers provide compile-time type safety and clear API contracts
2. **Code Generation**: Auto-generated stubs reduce boilerplate and bugs
3. **Bidirectional Streaming**: Native support for both Core → Integration and Integration → Core flows
4. **Performance**: Binary protocol + HTTP/2 multiplexing is significantly faster than JSON/HTTP/1.1
5. **Ecosystem**: Mature tooling (grpcurl for testing, grpc-gateway for REST compatibility, extensive documentation)
6. **Industry Standard**: Used by Google, Netflix, Square, etc. - proven at scale

**Trade-offs Accepted:**
- Binary protocol makes debugging harder (mitigated by grpcurl)
- Proto compilation adds build step (automated in setup)
- Learning curve is steeper (but documentation is excellent)

**Alternative for Future Consideration:**
If we need extreme scalability (1000+ integrations), we could layer NATS on top of gRPC for pub/sub patterns, but this is not needed for MVP.

---

## Bidirectional Architecture

**Decision:** Both Core and Integration run gRPC servers (bidirectional communication)

**Date:** 2024

**Context:**
Communication flows in both directions:
- **Integration → Core**: State updates, events, service calls
- **Core → Integration**: Service calls (turn_on/turn_off), lifecycle (start/stop), config flow

**Alternatives Considered:**

1. **Integration is only a gRPC server, Core is only a client**
   - Pros: Simpler - one-directional gRPC
   - Cons: How does Integration send state updates? Would need polling or callbacks

2. **Core is only a gRPC server, Integration is only a client**
   - Pros: Integration is simpler
   - Cons: How does Core send commands to Integration? Can't work.

3. **Both are client AND server** ✅
   - Pros: Natural bidirectional flow, each side exposes what it provides
   - Cons: Slightly more complex setup (two servers instead of one)

4. **Single gRPC connection with bidirectional streaming**
   - Pros: One connection to manage
   - Cons: Complex multiplexing of different message types, harder to reason about

**Decision Rationale:**

Having both Core and Integration run gRPC servers is the most natural architecture:

- **Core gRPC Server** exposes: `SetState`, `FireEvent`, `CallService`, `GetConfig`, etc.
  - Integration connects as a gRPC client to call these methods

- **Integration gRPC Server** exposes: `Initialize`, `Start`, `Stop`, `CallService`, etc.
  - Core connects as a gRPC client to call these methods

This provides:
1. **Clear separation of concerns**: Each side exposes what it owns
2. **Easy to reason about**: Standard RPC pattern, not custom multiplexing
3. **Flexible**: Can easily add new methods to either side
4. **Testable**: Can test each gRPC service independently

**Implementation Note:**
Both servers run in the same process (Core or Integration), so there's no additional process overhead.

---

## Core gRPC Server Always Running

**Decision:** Home Assistant Core always starts a gRPC server on port 50051, even if no integrations are in REMOTE mode

**Date:** 2024

**Context:**
Should the Core gRPC server be:
- Always running?
- Conditionally started (only if at least one integration is REMOTE)?
- Optional (user manually enables it)?

**Alternatives Considered:**

1. **Always running** ✅
   - Pros: Simple, no conditional logic, ready for any integration to go REMOTE
   - Cons: Adds gRPC as mandatory dependency, ~10MB RAM overhead

2. **Conditional (start only if needed)**
   - Pros: No overhead if all integrations are LOCAL
   - Cons: Complex detection logic ("do I need the server?"), must restart Core when first REMOTE integration is added

3. **Optional component (like `grpc_bridge`)**
   - Pros: Completely opt-in, zero impact if not used
   - Cons: User must remember to enable it, extra configuration step

**Decision Rationale:**

**Always running** is the best choice because:

1. **Simplicity**: No complex conditional logic or detection needed
2. **Ready to Use**: Integrations can switch to REMOTE mode without restarting Core
3. **Low Overhead**: Idle gRPC server consumes ~10MB RAM, negligible CPU
4. **Modern Standard**: Most modern systems already have gRPC for other services
5. **Future-Proof**: As more integrations become REMOTE, having the server ready reduces friction

**Trade-off Accepted:**
- gRPC becomes a core dependency (~10MB RAM when idle)
- This is acceptable because:
  - Modern HA installations have >1GB RAM available
  - The benefits (simplicity, flexibility) outweigh the cost
  - gRPC is a stable, well-maintained library

**Configuration:**
```yaml
# Optional configuration (defaults to localhost:50051)
grpc:
  port: 50051
  bind_address: localhost  # or 0.0.0.0 to expose externally
```

---

## LOCAL Mode Unchanged

**Decision:** Integrations in LOCAL mode remain 100% unchanged - no refactoring, no new dependencies, zero regression

**Date:** 2024

**Context:**
When introducing REMOTE mode, should we:
- Refactor all integrations to use a common abstraction?
- Keep LOCAL mode completely untouched?

**Alternatives Considered:**

1. **Refactor all integrations to use a Runtime abstraction**
   - Pros: Cleaner architecture, easier to maintain
   - Cons: Massive refactoring, high risk of regressions, affects all users

2. **Keep LOCAL mode completely unchanged** ✅
   - Pros: Zero risk, no regressions, gradual migration
   - Cons: Some code duplication during migration

**Decision Rationale:**

**Keep LOCAL unchanged** because:

1. **Zero Risk**: Existing integrations continue to work exactly as before
2. **No Regressions**: No chance of breaking existing setups
3. **Gradual Migration**: Migrate integrations one-by-one as needed
4. **User Confidence**: Users don't need to worry about stability

**How It Works:**

For integrations that haven't been migrated (the vast majority):
```python
# components/some_integration/__init__.py
# This file is NEVER touched
async def async_setup_entry(hass, entry):
    # Existing code, unchanged
    coordinator = DataUpdateCoordinator(...)
    # ...
```

For integrations that become "remote-ready":
```python
# components/pi_hole/__init__.py (NEW, replaces old version)
async def async_setup_entry(hass, entry):
    runtime_mode = entry.options.get('runtime_mode', 'local')
    
    if runtime_mode == 'local':
        runtime = LocalRuntime(hass, entry)  # Uses existing LOCAL code
    else:
        runtime = RemoteRuntime(hass, entry)  # New REMOTE code
    
    await runtime.initialize()
    hass.data[DOMAIN][entry.entry_id] = runtime
```

**Migration Pattern:**
1. Integration works in LOCAL mode (no changes)
2. Developer adds REMOTE support (adds `remote/` directory)
3. Integration continues to work in LOCAL mode by default
4. User can opt-in to REMOTE mode via config

---

## Opt-in Migration

**Decision:** Integrations are migrated to support REMOTE mode one-by-one, not all at once

**Date:** 2024

**Context:**
Should we:
- Migrate all integrations at once?
- Migrate one integration at a time?

**Decision Rationale:**

**One-by-one migration** because:

1. **Risk Management**: Test REMOTE mode thoroughly with one integration before expanding
2. **Resource Allocation**: Focus development effort where it provides most value
3. **Learning**: Each migration teaches us patterns to apply to next integration
4. **User Choice**: Users choose which integrations benefit from REMOTE mode

**Migration Priority:**

**Tier 1 (Ideal candidates):**
- Cloud APIs (Nest, Spotify, Weather)
- HTTP/REST integrations (Pi-hole, ESPHome)
- MQTT integrations (zigbee2mqtt)
- Simple polling integrations

**Tier 2 (Possible with limitations):**
- Webhook-based integrations
- Integrations with complex state

**Tier 3 (Stay LOCAL):**
- USB/Serial hardware integrations
- Bluetooth/BLE integrations
- High-frequency integrations (>10 updates/sec)

**Estimated Coverage:** 70-80% of integrations can eventually support REMOTE mode.

**Pattern for Each Migration:**
1. Extract business logic to `integration.py`
2. Add `runtime_local.py` wrapper
3. Add `remote/` directory with gRPC server
4. Update `manifest.json` with `"supports_remote": true`
5. Test thoroughly in both modes
6. Document any limitations

---

## No Changes to Integration Query Loop

**Decision:** In REMOTE mode, the integration manages its own polling loop - Core does NOT poll remote integrations

**Date:** 2024

**Context:**
Who manages the polling loop in REMOTE mode?
- Should Core poll the integration (like LOCAL mode with DataUpdateCoordinator)?
- Should the integration poll itself and push updates to Core?

**Alternatives Considered:**

1. **Core polls integration via gRPC**
   - Pros: Consistent with LOCAL mode
   - Cons: Core needs to track all remote integrations, complex coordination

2. **Integration polls itself and pushes to Core** ✅
   - Pros: Integration is self-contained, Core is passive receiver
   - Cons: Different from LOCAL mode pattern

**Decision Rationale:**

**Integration self-polls** because:

1. **Isolation**: Integration failure doesn't affect Core
2. **Simplicity**: Each integration manages its own schedule
3. **Flexibility**: Integrations can use different polling strategies
4. **Scalability**: Core doesn't need to track polling schedules

**Flow in REMOTE Mode:**

```
Integration Service (remote):
    while True:
        data = await integration.async_update()
        for entity_id, state in data.items():
            await core_client.SetState(entity_id, state, attributes)
        await asyncio.sleep(30)

Core (passive receiver):
    Receives SetState gRPC calls
    Updates StateStore
    Fires state_changed events
```

**Flow in LOCAL Mode (unchanged):**

```
Core:
    DataUpdateCoordinator polls:
        data = await integration.async_update()
        hass.states.async_set(entity_id, state, attributes)
```

**Trade-off:**
- Different patterns between LOCAL and REMOTE modes
- This is acceptable because the abstraction (RuntimeFactory) hides this difference from users

---

## Zero Code Duplication Principle

**Decision:** Integration code (`__init__.py`, `sensor.py`, `coordinator.py`, etc.) must be identical in LOCAL and REMOTE modes - absolutely zero duplication

**Date:** 2024

**Context:**
How do we make integrations work in REMOTE mode without duplicating code?

**Initial Approach (Wrong):**
- Extract business logic to `integration.py`
- Create `runtime_local.py` wrapper
- Create `runtime_remote.py` wrapper with gRPC
- Problem: Lots of code duplication, rewriting setup logic, entity creation, etc.

**Correct Approach:** ✅
- Keep integration code **completely unchanged**
- In REMOTE mode, provide a `HomeAssistantGrpcProxy` instead of real `HomeAssistant`
- Proxy exposes **exact same interface**, but calls gRPC under the hood
- Integration code cannot tell the difference

**Decision Rationale:**

This approach provides:

1. **Zero Duplication**: The same `async_setup_entry()` runs in both modes
2. **Zero Refactoring**: No need to extract or rewrite existing integration code
3. **Transparency**: Integration code doesn't know if it's LOCAL or REMOTE
4. **Simplicity**: Just swap the `hass` object implementation
5. **Maintainability**: One codebase, no drift between LOCAL and REMOTE

**How It Works:**

```python
# Integration code (UNCHANGED - same in both modes)
async def async_setup_entry(hass, entry):
    coordinator = DataUpdateCoordinator(hass, ...)
    
    sensors = [PiHoleSensor(coordinator, "ads_blocked")]
    async_add_entities(sensors)
    
    async def async_disable_service(call):
        await coordinator.api.disable()
    
    hass.services.async_register(DOMAIN, "disable", async_disable_service)
```

**In LOCAL mode:**
```python
hass = HomeAssistant()  # Real object
await async_setup_entry(hass, entry)
# hass.states.async_set() → Direct Python call
# hass.services.async_register() → Direct Python call
```

**In REMOTE mode:**
```python
hass = HomeAssistantGrpcProxy(core_address='localhost:50051')  # Proxy object
await async_setup_entry(hass, entry)  # SAME FUNCTION
# hass.states.async_set() → gRPC call to Core
# hass.services.async_register() → gRPC call to Core
```

**Remote Entry Point:**

```python
# remote/main.py
async def main(core_address, entry_id, config):
    # Create proxy hass
    hass = HomeAssistantGrpcProxy(core_address)
    
    # Create config entry (same structure as LOCAL)
    entry = ConfigEntry(entry_id=entry_id, domain=DOMAIN, data=config)
    
    # Import and run SAME setup code
    from homeassistant.components.pi_hole import async_setup_entry
    await async_setup_entry(hass, entry)
    
    # Integration is now running, using proxy hass
    await asyncio.sleep_forever()
```

**Proxy Implementation:**

```python
class HomeAssistantGrpcProxy:
    """Drop-in replacement for HomeAssistant that proxies via gRPC"""
    
    def __init__(self, core_address: str):
        self.core_stub = CoreServiceStub(grpc.insecure_channel(core_address))
        self.states = StatesProxy(self.core_stub)
        self.bus = EventBusProxy(self.core_stub)
        self.services = ServicesProxy(self.core_stub)
        # ... all other hass.* attributes

class StatesProxy:
    async def async_set(self, entity_id, state, attributes=None):
        await self.core_stub.SetState(entity_id=entity_id, state=state, attributes=attributes)
```

**Trade-offs:**
- Need to implement full `hass` interface in proxy (significant work)
- But this is done ONCE and benefits ALL integrations
- Acceptable because it eliminates duplication in 100s of integrations

**What This Means for Integration Migration:**

To make an integration remote-ready:

1. Add `remote/` directory with:
   - `main.py` (entry point)
   - `hass_proxy.py` (reusable across integrations)
2. That's it. No changes to existing code.

**What This Eliminates:**
- ❌ NO `integration.py` extraction
- ❌ NO `runtime_local.py` / `runtime_remote.py` wrappers
- ❌ NO duplication of setup logic
- ❌ NO rewriting entity creation
- ❌ NO separate coordinator implementation

**The integration just works in both modes.**

---

**Decision:** Create separate Executor classes (ProcessExecutor, DockerExecutor, KubernetesDedicatedExecutor, KubernetesWorkerExecutor) to manage integration lifecycle

**Date:** 2024

**Context:**
Remote integrations can run as:
- Local subprocess
- Docker container
- Kubernetes pod (dedicated - 1 integration per pod)
- Kubernetes pod (worker - multiple integrations per pod)

How do we abstract this?

**Alternatives Considered:**

1. **Single Executor with mode parameter**
   - Pros: One class to maintain
   - Cons: Complex if/else logic for different modes

2. **Separate Executor classes** ✅
   - Pros: Clean separation, easy to add new executors, testable
   - Cons: Slightly more code

**Decision Rationale:**

**Separate Executor classes** provide:

1. **Single Responsibility**: Each executor handles one deployment model
2. **Open/Closed**: Easy to add new executors (e.g., WebAssembly, Lambda) without modifying existing code
3. **Testability**: Mock executors for testing
4. **Clarity**: Clear what each executor does

**Interface:**

```python
class ExecutorBase(ABC):
    @abstractmethod
    async def start(self, integration: str, config: dict) -> str:
        """Start integration and return gRPC address"""
        pass
    
    @abstractmethod
    async def stop(self):
        """Stop integration gracefully"""
        pass
```

**Implementations:**
- `ProcessExecutor`: Launches subprocess, finds free port
- `DockerExecutor`: Creates container with resource limits
- `KubernetesDedicatedExecutor`: Creates dedicated Pod + Service per integration
- `KubernetesWorkerExecutor`: Finds/creates Worker Pod, calls Initialize on existing worker

**Usage:**

```python
class RemoteRuntime:
    def __init__(self, hass, config, executor: ExecutorBase):
        self.executor = executor
    
    async def initialize(self):
        grpc_address = await self.executor.start('pi_hole', config)
        self.grpc_stub = connect_to(grpc_address)
```

---

## Worker vs Dedicated Pods

**Decision:** Support both dedicated pods (1 integration = 1 pod) AND worker pods (N integrations = 1 pod)

**Date:** 2024

**Context:**
For Kubernetes deployments, should we:
- Always create 1 pod per integration (dedicated)?
- Always share pods between integrations (worker)?
- Support both modes?

**Problem with Dedicated-Only:**
- With 50 integrations → 50 pods
- High resource overhead (even with small requests/limits)
- Cluster sprawl

**Problem with Worker-Only:**
- No isolation between integrations
- One crash affects all integrations in worker
- Harder to debug and manage

**Alternatives Considered:**

1. **Dedicated pods only**
   - Pros: Maximum isolation, simple routing
   - Cons: High resource overhead at scale

2. **Worker pods only**
   - Pros: Very efficient resource usage
   - Cons: No isolation, complex routing

3. **Support both, user chooses** ✅
   - Pros: Flexibility, optimize per use case
   - Cons: Two implementations to maintain

**Decision Rationale:**

**Support both modes** because different integrations have different needs:

**Use Dedicated for:**
- Mission-critical integrations (alarm, security)
- Resource-intensive integrations
- Integrations that need full isolation
- Production-grade deployments with strict SLAs

**Use Worker for:**
- Many lightweight integrations (sensors, switches)
- Cost optimization (cloud K8s billing)
- Development/testing environments
- Non-critical integrations

**Implementation:**

```python
class RuntimeMode(Enum):
    LOCAL = "local"
    REMOTE_DEDICATED = "remote_dedicated"  # 1 pod per integration
    REMOTE_WORKER = "remote_worker"        # Shared worker pod
```

**UI Configuration:**

```yaml
# Dedicated mode
Integration: Alarm System
Runtime Mode: Remote Dedicated
Executor: Kubernetes

# Worker mode
Integration: Temperature Sensor
Runtime Mode: Remote Worker
Worker: worker-1  # Select existing or create new
```

**Worker Architecture:**

```python
class WorkerGrpcServer:
    """Multi-tenant gRPC server running in Worker Pod"""
    
    def __init__(self):
        self.integrations = {}  # {integration_id: Integration instance}
    
    async def Initialize(self, request, context):
        integration_id = request.integration_id
        integration_type = request.integration_type  # "pi_hole", "esphome"
        
        # Load integration class dynamically
        IntegrationClass = load_integration_class(integration_type)
        integration = IntegrationClass(request.config)
        
        self.integrations[integration_id] = integration
        await integration.async_initialize()
    
    async def CallService(self, request, context):
        # Route by integration_id
        integration = self.integrations[request.integration_id]
        await integration.call_service(request.service, request.data)
```

**Protocol Changes:**

All messages require `integration_id` for routing in worker mode:

```protobuf
message CallServiceRequest {
  string integration_id = 1;    // Required for worker routing
  string entity_id = 2;
  string service = 3;
  map<string, string> data = 4;
}
```

**Trade-offs Accepted:**
- More complexity (two K8s executors)
- Worker routing requires integration_id in all messages
- This is acceptable because it provides flexibility where needed

**Phasing:**
- Phase 5: Implement KubernetesExecutor (unified — replaces former KubernetesDedicatedExecutor + KubernetesWorkerExecutor)
- Users choose `max_integrations_per_worker=1` for dedicated behavior or `N` for worker pool behavior

---

## Service Call Routing

**Decision:** Core maintains a registry of entity_id → integration mapping to route service calls correctly

**Date:** 2024

**Context:**
When a user calls `switch.turn_on`, how does Core know:
- Is this entity LOCAL or REMOTE?
- If REMOTE, what's the gRPC address?

**Alternatives Considered:**

1. **Entity ID prefix**
   - Entity IDs like `remote.switch.pihole` vs `local.switch.pihole`
   - Cons: Ugly, breaks existing entity IDs

2. **Service Registry at startup**
   - Integration registers services with Core
   - Cons: Complex, what if integration crashes?

3. **Entity → Integration mapping** ✅
   - Core maintains `{entity_id: (entry_id, grpc_address)}`
   - Pros: Clean, transparent to user

**Decision Rationale:**

**Entity mapping** is the cleanest approach:

```python
# Core
class HomeAssistant:
    def __init__(self):
        self._entity_to_integration = {}  # {entity_id: entry_id}
        self._remote_integrations = {}    # {entry_id: grpc_stub}
    
    def register_remote_integration(self, entry_id, grpc_stub, entity_ids):
        self._remote_integrations[entry_id] = grpc_stub
        for entity_id in entity_ids:
            self._entity_to_integration[entity_id] = entry_id
    
    async def async_call_service(self, domain, service, data):
        entity_id = data.get('entity_id')
        entry_id = self._entity_to_integration.get(entity_id)
        
        if entry_id and entry_id in self._remote_integrations:
            # REMOTE call
            stub = self._remote_integrations[entry_id]
            await stub.CallService(entity_id=entity_id, service=service)
        else:
            # LOCAL call (existing code)
            await self._call_local_service(domain, service, data)
```

**Registration Flow:**

```
Integration (remote) starts
    ↓
Integration.Initialize() returns list of entity_ids
    ↓
Core.register_remote_integration(entry_id, grpc_stub, entity_ids)
    ↓
Core can now route service calls
```

**Trade-off:**
- Core needs to maintain this mapping
- Acceptable because it's small (hundreds of entries max) and critical for functionality

---

## POC Scope

**Decision:** Phase 0 (POC) implements only the absolute minimum to prove the concept

**Date:** 2024

**Context:**
What should be included in the POC?

**What's IN the POC:**
✅ Core gRPC Server with `SetState` only
✅ Integration gRPC Server with `Initialize`, `Start`, `CallService`
✅ PiHoleIntegration business logic (pure, no HA dependencies)
✅ Hass proxy (`states.async_set` via gRPC)
✅ Manual launch (no executors)
✅ Basic testing with grpcurl

**What's OUT of the POC:**
❌ Event bus
❌ Service registry
❌ Config flow
❌ Entity/Device registry
❌ ProcessExecutor/DockerExecutor/KubernetesExecutor
❌ Reconnection logic
❌ Error handling
❌ Comprehensive tests
❌ Production deployment

**Decision Rationale:**

The POC should:
1. **Prove the concept**: Can Core and Integration communicate bidirectionally via gRPC?
2. **Validate the approach**: Is the business logic truly reusable?
3. **Be achievable**: ~8 hours of development time
4. **Provide learnings**: What works? What doesn't?

**Success Criteria:**
- Pi-hole runs in separate process
- States sync from Integration → Core
- Commands work from Core → Integration
- Business logic has zero HA dependencies

If POC succeeds, we proceed to Phase 1 (ProcessExecutor + full API).

If POC fails, we re-evaluate the approach before investing more time.

---

## Generic Worker vs Specific Entry Points

**Decision:** Implement a single generic worker (`homeassistant/worker/main.py`) capable of loading any integration, rather than a specific entry point per integration (e.g. `pi_hole/remote/main.py`).

**Date:** 2024

**Context:**
The POC proved the concept with `pi_hole/remote/main.py`. But this approach requires a `remote/main.py` file per integration, which violates the Zero Code Duplication Principle and makes migration tedious.

**Alternatives Considered:**

1. **One `remote/main.py` per integration** ❌ — duplication, integrations must be modified
2. **A generic worker** ✅ — zero modification of integrations, a single runtime to maintain

**Decision Rationale:**

The generic worker is the only approach compatible with the principle "the integration does not know it is remote". The `HomeAssistantGrpcProxy` is enriched to be sufficiently transparent.

**Consequence:** `pi_hole/remote/main.py` will be removed in Phase 1.

---

## Configuration: Pull (Worker requests) vs Push (Core sends)

**Decision:** The worker uses the **Pull** pattern — it connects to the Core and calls `GetEntry(entry_id)` to obtain its configuration, rather than receiving the config via CLI arguments or via Push from the Core.

**Date:** 2024

**Context:**
How does the worker receive the config (`entry.data`, `entry.options`, `domain`)?

**Alternatives Considered:**

1. **CLI args** (current POC) — config passed as JSON on the command line. ❌ Insecure (secrets visible in `ps`), limited in size, no dynamic updates
2. **Push (Core → Worker)** — Core sends `SetupEntry(domain, config)` after the worker starts. ❌ Complex, requires temporal coordination
3. **Pull (Worker → Core)** ✅ — Worker calls `GetEntry(entry_id)`, Core returns `{domain, config, options}`. Simple, secure (no secrets in args), supports updates

**Decision Rationale:**

Pull is cleaner — the worker is autonomous and knows what to ask for with just an `entry_id`. Secrets do not pass through process arguments.

**Implementation:** New `rpc GetEntry(GetEntryRequest) returns (GetEntryResponse)` in the proto. Core stores the `ConfigEntry` objects and serves them on demand.

---

## Strategy for HA Registries (Device, Entity, Issue, Area)

**Decision:** Implement **local shims** (lightweight mocks) for HA registries in the worker, rather than fully proxying them via gRPC.

**Date:** 2024

**Context:**
Integrations use `device_registry`, `entity_registry`, `issue_registry`, `area_registry`. Should these be proxied via gRPC or simulated locally?

**Alternatives Considered:**

1. **Full gRPC proxy** — each registry call → gRPC to Core. ❌ High latency, complex proto, difficult bidirectional synchronization
2. **Local shims** ✅ — lightweight mocks that return acceptable default values. Writes = no-op or fire-and-forget. Reads = empty/default values.
3. **Sync on startup** — Core sends a snapshot of the registries when the worker starts. 🟡 More complete but complex, reserved for a future phase.

**Decision Rationale:**

For Phase 1, shims are sufficient. The majority of integrations write to registries (registering entities/devices) but do not read back critically. Critical reads (e.g. `async_entries_for_config_entry`) can return empty lists without breaking functionality.

**Limit:** Entities will not be in the HA entity registry (no advanced UI management). Acceptable for Phase 1, to be improved in Phase 3 (Complete Core API).

---

## Local Hardware in Remote Workers

**Decision:** Integrations requiring hardware (Bluetooth, USB, Serial) are supported if and only if the worker runs on a machine that has that hardware.

**Date:** 2024

**Context:**
The initial analysis marked Bluetooth/USB/Serial as "impossible" for remote workers.

**Clarification:**

This is not a limitation of the gRPC proxy — it is a topology constraint. If the worker runs on a Raspberry Pi machine with a Bluetooth dongle, the Bluetooth integration works normally. This is even a primary use case: offloading a Bluetooth integration to a RPi in a remote room.

**Consequence on Architecture:** None — the proxy does not need to handle hardware. It is transparent.

---

## Config Flows: Execution in Core Only

**Decision:** Config flows (and option flows) run entirely in HA Core, never in the worker.

**Date:** 2024

**Context:**
A config flow creates a `ConfigEntry`. Then `async_setup_entry` is called. The question is: where does the config flow run?

**Decision:**

The config flow runs in the Core as it does today. Once the flow is complete and the `ConfigEntry` is created, the `RuntimeFactory` decides whether `async_setup_entry` runs locally (LOCAL) or triggers a worker (REMOTE).

**Consequence:** Zero changes to existing config flows. The RuntimeFactory is the single routing point for LOCAL/REMOTE.

**Phase:** Handling interactive config flows from the worker (e.g. reauth) is deferred to a future phase.

---

## Support for Custom Integrations — ComponentResolver

**Decision:** The worker supports custom integrations (HACS, GitHub) via a `ComponentResolver` that downloads and installs the integration locally in the worker.

**Context:** In normal HA, custom integrations are in `/config/custom_components/`. A remote worker does not have access to this folder.

**Alternatives Considered:**

1. **Filesystem sharing** (NFS/shared volume) — Simple but tightly coupled, does not work for K8s or remote workers.

2. **Pull from Core via gRPC** (`GetCustomComponent(domain)` → source code) — Complex, questionable security (executing code received over the network).

3. **ComponentResolver with GitHub/HACS source** ✅ — The Core sends the `source` in `GetEntry`. The worker downloads and installs locally. Compatible with all executor phases.

4. **Pre-built Docker image** — Good for DockerExecutor/K8s but does not solve the ProcessExecutor case and requires a rebuild on every update.

**Decision Rationale:**
- The Core is the source of truth on the origin of an integration (it installed it)
- GitHub/HACS are already the canonical source for custom components
- The `ComponentResolver` is naturally compatible with HACS (which uses GitHub)
- Versioning (`@tag`, `@commit`) guarantees reproducibility between Core and worker
- The local cache avoids re-downloads on every worker restart

**Source of Truth:** The Core sends `source` in `GetEntryResponse`. The worker never decides where an integration comes from.

**Implementation:** New phase 7, `homeassistant/worker/resolver.py`.

---

## Multiple Integrations from Phase 1

**Decision:** Multi-integration support in a single worker is implemented from Phase 1 (generic worker) onwards, not deferred to Phase 5.

**Context:** Phase 5 (KubernetesExecutor) initially had in its scope "Worker Pod implementation (multi-tenant gRPC server)". This implied that multi-integration support would be a Phase 5 feature.

**Correction:** The Phase 1 generic worker natively supports multiple integrations — it receives a list of `entry_id` values and loads each integration independently with its own `_MinimalConfigEntry`. The `HomeAssistantGrpcProxy` is shared across all integrations in the same worker.

**Consequence:**
- Phase 1: multi-integration generic worker ✅
- Phase 5: Kubernetes orchestration only (pool management, routing, auto-scaling)

---

## KubernetesDedicatedExecutor Removed — Merged into KubernetesExecutor

**Decision:** Remove `KubernetesDedicatedExecutor` as a separate phase/executor. A single `KubernetesExecutor` with a `max_integrations_per_worker` parameter covers both use cases.

**Context:** The initial architecture had two separate Kubernetes executors:
- `KubernetesDedicatedExecutor` — one pod per integration (maximum isolation)
- `KubernetesWorkerExecutor` — multiple integrations per pod (maximum efficiency)

**Problem:** This distinction is artificial. The generic worker (Phase 1) already supports multiple integrations natively. A dedicated pod per integration is just a worker with `max_integrations_per_worker=1`.

**Decision:** A single `KubernetesExecutor` with:
- `max_integrations_per_worker: 1` → "dedicated" behavior (maximum isolation)
- `max_integrations_per_worker: N` → "worker pool" behavior (maximum efficiency)
- `max_integrations_per_worker: 0` → unlimited (default)

**Consequence:** Roadmap simplified from 8 phases to 7 phases. Less code to maintain.

---

## Worker Configuration as a Dedicated Phase

**Decision:** Worker declaration in `configuration.yaml` is a dedicated phase (Phase 2) between the Generic Worker (Phase 1) and the DockerExecutor (Phase 4).

**Context:** After Phase 1, workers are launched implicitly (one subprocess per integration). Users have no way to declare persistent workers, assign multiple integrations to them, or configure Docker/Kubernetes workers.

**What this phase adds:**
- Explicit worker declaration in `configuration.yaml`
- Four worker types: `process`, `docker`, `remote`, `kubernetes`
- Worker lifecycle management (startup/shutdown per type)
- Config flow integration: worker selection dropdown
- Capacity management (`max_integrations`)
- RBAC validation for Kubernetes workers

**Key decisions:**
- `process` workers: permanent subprocess started at HA startup (not on-demand per integration)
- `docker` workers: always recreated at startup (stop existing + create new) — no state reuse
- `remote` workers: HA only connects, never manages lifecycle
- `kubernetes` workers: in-cluster only (ClusterIP Service), always recreated at startup
- Docker `worker_address` auto-deduced from `host` IP + `port`
- Kubernetes `worker_address` auto-deduced as ClusterIP Service DNS name
- If no workers declared: config flow offers LOCAL only (no change to existing behavior)
- Worker unavailable at config flow time: show error, block entry creation

**Reference:** See `horizontal_scaling/WORKERS.md` for full configuration documentation.

---

## Summary

These decisions form the foundation of the Runtime Pluggable architecture:

1. **gRPC** provides the best balance of performance, type safety, and ecosystem
2. **Bidirectional** architecture is the most natural fit for Core ↔ Integration communication
3. **Always-on gRPC server** in Core provides simplicity and flexibility
4. **LOCAL mode unchanged** eliminates risk and allows gradual migration
5. **Opt-in migration** manages risk and focuses effort where it matters
6. **Integration self-polling** in REMOTE mode provides isolation and simplicity
7. **Executor abstraction** allows flexibility in deployment models
8. **Entity mapping** provides clean service call routing
9. **Minimal POC** proves the concept without over-investing upfront
10. **Generic worker** eliminates per-integration entry points and upholds the Zero Code Duplication Principle
11. **Pull-based config** keeps secrets out of process arguments and enables dynamic config updates
12. **Local registry shims** provide lightweight compatibility for Phase 1 without complex gRPC proxying
13. **Hardware transparency** — remote workers on hardware-equipped machines support Bluetooth/USB/Serial natively
14. **Config flows stay in Core** — the RuntimeFactory is the single routing point; flows need zero modification
15. **ComponentResolver** — workers download and install custom integrations (HACS/GitHub) locally; the Core is the single source of truth for integration origin via the `source` field in `GetEntryResponse`
16. **Multi-integration support in Phase 1** — the generic worker natively supports multiple integrations in a single process; Phase 5 (KubernetesExecutor) only adds the Kubernetes orchestration layer (pool management, routing, auto-scaling) on top
17. **KubernetesExecutor unified** — a single `KubernetesExecutor` with `max_integrations_per_worker` replaces the former `KubernetesDedicatedExecutor` and `KubernetesWorkerExecutor`; dedicated pod behavior is just `max_integrations_per_worker=1`, simplifying the roadmap from 8 to 7 phases

These decisions can be revisited as we learn more from implementation and production use.
