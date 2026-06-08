# Phase 0: Proof of Concept (POC)

## Objective

**Prove that a Home Assistant integration (Pi-hole) can run in a separate process using a transparent gRPC proxy for the `hass` object, with the Core deciding runtime mode and ZERO code duplication in the integration.**

Success means:
1. Core decides runtime mode BEFORE calling `async_setup_entry()`
2. Pi-hole integration code (`__init__.py`, `coordinator.py`, `sensor.py`) runs UNCHANGED in REMOTE mode
3. Integration uses `HomeAssistantGrpcProxy` transparently (doesn't know it's remote)
4. Core receives entity state updates via gRPC
5. Service handlers registered by integration work correctly
6. Coordinator polling works identically to LOCAL mode
7. All code resides in `homeassistant/` directory (no separate POC folder)

## Scope

### What We Implement

✅ **Core gRPC Server** (minimal)
- `SetState(entity_id, state, attributes)` - Receive state updates from integration
- `RegisterService(domain, service)` - Register service handlers

✅ **Core Runtime Dispatcher**
- Logic to decide LOCAL vs REMOTE mode
- Start ProcessExecutor for REMOTE mode

✅ **ProcessExecutor** (minimal)
- Launch subprocess running integration
- Pass Core gRPC address to subprocess

✅ **HomeAssistantGrpcProxy** (minimal)
- `hass.states.async_set()` → gRPC `SetState()`
- `hass.services.async_register()` → gRPC `RegisterService()`
- Enough to make Pi-hole work

✅ **Remote Entry Point**
- `homeassistant/components/pi_hole/remote/main.py`
- Creates proxy hass, imports and calls `async_setup_entry()`

✅ **Integration Code**
- Use EXISTING `__init__.py`, `coordinator.py`, `sensor.py`, `switch.py`
- ZERO modifications to these files

✅ **Manual Testing**
- Launch Core normally (auto-starts gRPC server)
- Configure Pi-hole with runtime_mode='remote'
- Verify states sync, services work

### What We Do NOT Implement

❌ Event bus (not critical for Pi-hole)
❌ Full service registry (just basic registration)
❌ Config flow / Options flow (hardcode config for POC)
❌ Entity registry / Device registry
❌ Translations
❌ WebSocket handlers
❌ Docker/Kubernetes executors (just Process for POC)
❌ Reconnection / Retry logic
❌ Comprehensive error handling
❌ Tests
❌ Full `HomeAssistantGrpcProxy` implementation (only what Pi-hole needs)

**Rationale:** Focus on proving the core concept with minimum code.

## Architecture

```
┌─────────────────────────────────────────┐
│   Home Assistant Core                  │
│                                         │
│   Runtime Dispatcher                   │
│   - Checks runtime_mode                │
│   - If LOCAL: import & call setup      │
│   - If REMOTE: start ProcessExecutor   │
│                                         │
│   StateStore (dict)                    │
│   ServiceRegistry (dict)               │
│            ↑                            │
│   CoreGrpcServer                       │
│   - SetState()                         │
│   - RegisterService()                  │
│   Port: 50051                          │
└─────────────────────────────────────────┘
            ↑
            │ gRPC
            │
            ↓
┌─────────────────────────────────────────┐
│   Process: Pi-hole Integration          │
│   (started by ProcessExecutor)          │
│                                         │
│   remote/main.py:                       │
│   - Creates HomeAssistantGrpcProxy     │
│   - Imports async_setup_entry          │
│   - Calls async_setup_entry(proxy)     │
│            ↓                            │
│   Integration Code (UNCHANGED):        │
│   - __init__.py                         │
│   - coordinator.py                      │
│   - sensor.py                           │
│   - switch.py                           │
│            ↓                            │
│   Uses hass.states.async_set()         │
│        → HomeAssistantGrpcProxy        │
│        → gRPC SetState() to Core       │
└─────────────────────────────────────────┘
```

## File Structure

**All code in `homeassistant/` directory (no separate POC folder):**

```
homeassistant/
├── components/
│   └── pi_hole/
│       ├── __init__.py          # EXISTING - UNCHANGED
│       ├── sensor.py            # EXISTING - UNCHANGED
│       ├── switch.py            # EXISTING - UNCHANGED
│       ├── coordinator.py       # EXISTING - UNCHANGED
│       ├── manifest.json        # EXISTING - Add "supports_remote": true
│       └── remote/              # NEW
│           ├── __init__.py
│           └── main.py          # Entry point for remote execution
│
├── grpc/                        # NEW
│   ├── __init__.py
│   ├── server.py                # CoreGrpcServer
│   ├── protos/
│   │   ├── core.proto           # Proto definitions
│   │   └── (generated files)
│   └── services/
│       └── state_service.py     # SetState implementation
│
├── executors/                   # NEW
│   ├── __init__.py
│   ├── base.py                  # ExecutorBase
│   └── process.py               # ProcessExecutor
│
└── helpers/
    └── remote_hass.py           # NEW - HomeAssistantGrpcProxy

```

## Protocol Definitions

### core.proto

**Minimal for POC - only what Pi-hole needs:**

```protobuf
syntax = "proto3";

package homeassistant.core;

service CoreService {
  // State Management
  rpc SetState(SetStateRequest) returns (SetStateResponse);
  
  // Service Registration (for hass.services.async_register)
  rpc RegisterService(RegisterServiceRequest) returns (RegisterServiceResponse);
}

message SetStateRequest {
  string entity_id = 1;
  string state = 2;
  map<string, string> attributes = 3;
}

message SetStateResponse {
  bool success = 1;
  string error = 2;
}

message RegisterServiceRequest {
  string domain = 1;
  string service = 2;
  // Handler will be called in remote process, registered here for tracking
}

message RegisterServiceResponse {
  bool success = 1;
  string error = 2;
}
```

**Note:** We don't need `integration.proto` anymore - no Integration gRPC server!

## Component Specifications

### 1. Core Runtime Dispatcher

**Location:** `homeassistant/config_entries.py` (modify existing)

Adds logic to check runtime mode before calling `async_setup_entry()`:

```python
async def async_setup(hass, entry):
    """Set up a config entry."""
    
    runtime_mode = entry.options.get('runtime_mode', 'local')
    
    if runtime_mode == 'local':
        # LOCAL: Standard in-process setup
        component = await hass.async_import_component(entry.domain)
        result = await component.async_setup_entry(hass, entry)
    
    elif runtime_mode == 'remote':
        # REMOTE: Start via ProcessExecutor
        from homeassistant.executors.process import ProcessExecutor
        
        executor = ProcessExecutor()
        await executor.start(
            domain=entry.domain,
            entry_id=entry.entry_id,
            config=entry.data
        )
        result = True
    
    return result
```

### 2. Core gRPC Server

**Location:** `homeassistant/grpc/server.py`

Minimal gRPC server with state management:

```python
class CoreGrpcServer:
    def __init__(self, hass):
        self.hass = hass
        self.server = None
    
    async def start(self, port=50051):
        """Start gRPC server"""
        from homeassistant.grpc.services.state_service import StateService
        
        self.server = grpc.aio.server()
        
        # Add services
        state_service = StateService(self.hass)
        core_pb2_grpc.add_CoreServiceServicer_to_server(state_service, self.server)
        
        self.server.add_insecure_port(f'[::]:{port}')
        await self.server.start()
    
    async def stop(self):
        if self.server:
            await self.server.stop(grace=5)
```

**Location:** `homeassistant/grpc/services/state_service.py`

```python
class StateService(core_pb2_grpc.CoreServiceServicer):
    def __init__(self, hass):
        self.hass = hass
    
    async def SetState(self, request, context):
        """Handle SetState from remote integration"""
        # Call real hass.states.async_set
        self.hass.states.async_set(
            request.entity_id,
            request.state,
            dict(request.attributes)
        )
        return core_pb2.SetStateResponse(success=True)
    
    async def RegisterService(self, request, context):
        """Track that a service was registered (handler runs remotely)"""
        # For POC, just log it
        print(f"Service registered: {request.domain}.{request.service}")
        return core_pb2.RegisterServiceResponse(success=True)
```

### 3. ProcessExecutor

**Location:** `homeassistant/executors/process.py`

```python
class ProcessExecutor:
    async def start(self, domain: str, entry_id: str, config: dict):
        """Start integration in subprocess"""
        
        core_address = 'localhost:50051'
        
        cmd = [
            sys.executable,
            '-m', f'homeassistant.components.{domain}.remote.main',
            '--core-address', core_address,
            '--entry-id', entry_id,
            '--config', json.dumps(config)
        ]
        
        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        # Wait a bit for process to start
        await asyncio.sleep(2)
    
    async def stop(self):
        if self.process:
            self.process.terminate()
            await self.process.wait()
```

### 4. HomeAssistantGrpcProxy (Minimal)

**Location:** `homeassistant/helpers/remote_hass.py`

Only implements what Pi-hole integration needs:

```python
class HomeAssistantGrpcProxy:
    """Minimal proxy - only what Pi-hole needs"""
    
    def __init__(self, core_address: str):
        self.channel = grpc.aio.insecure_channel(core_address)
        self.stub = core_pb2_grpc.CoreServiceStub(self.channel)
        
        self.states = StatesProxy(self.stub)
        self.services = ServicesProxy(self.stub)
        self.data = {}
        self.config_entries = MockConfigEntries()
        # Add other attributes as needed
    
    async def async_create_task(self, coro):
        return asyncio.create_task(coro)

class StatesProxy:
    def __init__(self, stub):
        self.stub = stub
    
    async def async_set(self, entity_id: str, state: str, attributes: dict = None):
        await self.stub.SetState(core_pb2.SetStateRequest(
            entity_id=entity_id,
            state=state,
            attributes={k: str(v) for k, v in (attributes or {}).items()}
        ))

class ServicesProxy:
    def __init__(self, stub):
        self.stub = stub
    
    async def async_register(self, domain: str, service: str, handler):
        # Register with core (handler runs locally in remote process)
        await self.stub.RegisterService(core_pb2.RegisterServiceRequest(
            domain=domain,
            service=service
        ))
        # Store handler locally for when it's called
        # (For POC, handlers are called by integration's own logic)
```

### 5. Remote Entry Point

**Location:** `homeassistant/components/pi_hole/remote/main.py`

```python
import asyncio
import argparse
import json
import sys

# Add homeassistant to path
sys.path.insert(0, '/path/to/homeassistant')

async def main(core_address: str, entry_id: str, config: dict):
    """Run Pi-hole integration remotely"""
    
    from homeassistant.helpers.remote_hass import HomeAssistantGrpcProxy
    from homeassistant.config_entries import ConfigEntry
    
    # Create proxy hass
    hass = HomeAssistantGrpcProxy(core_address)
    
    # Create config entry
    entry = ConfigEntry(
        version=1,
        domain='pi_hole',
        title='Pi-hole',
        data=config,
        source='user',
        entry_id=entry_id,
        options={}
    )
    
    # Import and run setup (SAME code as LOCAL mode)
    from homeassistant.components.pi_hole import async_setup_entry
    
    print(f"Starting Pi-hole integration remotely...")
    success = await async_setup_entry(hass, entry)
    
    if not success:
        print("Setup failed!")
        return
    
    print("Pi-hole integration running remotely. Press Ctrl+C to stop.")
    
    # Keep running
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("Shutting down...")

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--core-address', required=True)
    parser.add_argument('--entry-id', required=True)
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    
    config = json.loads(args.config)
    asyncio.run(main(args.core_address, args.entry_id, config))
```

## Dependencies (requirements.txt)

```
grpcio>=1.60.0
grpcio-tools>=1.60.0
hole>=0.7.0
```

## Setup Instructions

### 1. Install Dependencies

```bash
cd poc
pip install -r requirements.txt
```

### 2. Generate Protobuf Code

```bash
cd protos
python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. core.proto
python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. integration.proto
```

This generates:
- `core_pb2.py`
- `core_pb2_grpc.py`
- `integration_pb2.py`
- `integration_pb2_grpc.py`

### 3. Start Home Assistant Core

In Terminal 1:

```bash
cd poc/core
python main.py
```

Expected output:
```
[Core] gRPC Server started on port 50051
[Core] Waiting for integration to connect...
```

### 4. Start Pi-hole Integration Service

In Terminal 2:

```bash
cd poc/integration
python main.py --host 192.168.1.10 --api-key YOUR_API_KEY
```

Replace `192.168.1.10` with your Pi-hole's IP address.

Expected output:
```
[Main] Starting Pi-hole Integration Service
[Main] Pi-hole: 192.168.1.10
[Main] Core: localhost:50051
[Main] Port: 50052
[Integration] gRPC Server started on port 50052
[Integration] Connected to Core at localhost:50051
[Integration] Initialize request: pihole_poc
[Integration] Config: {'host': '192.168.1.10', 'api_key': '...', 'ssl': 'false'}
[Integration] Connected to Pi-hole at 192.168.1.10
[Main] Integration initialized. Entities: ['sensor.pihole_ads_blocked_today', ...]
[Main] Integration started successfully
[Integration] Starting update loop (30s interval)
[Integration] Updated: 1234 ads blocked
```

In Terminal 1 (Core), you should see:
```
[Core] SetState: sensor.pihole_ads_blocked_today = 1234
[Core] Attributes: {'domains_blocked': '50000', 'queries_today': '5000'}
[Core] SetState: switch.pihole = on
...
```

## Testing

### Test 1: Verify State Updates

Watch Terminal 1 (Core) for state updates every 30 seconds.

Expected: New `SetState` calls logged every 30s with updated Pi-hole stats.

### Test 2: Call a Service (Turn Off)

In Terminal 3:

```bash
cd poc/protos
grpcurl -plaintext -d '{
  "integration_id": "pihole_poc",
  "entity_id": "switch.pihole",
  "service": "turn_off"
}' localhost:50052 integration.IntegrationService/CallService
```

Expected output:
```json
{
  "success": true
}
```

In Terminal 2 (Integration):
```
[Integration] CallService: turn_off on switch.pihole
[Integration] Turning Pi-hole OFF
[Integration] Updated: 1234 ads blocked
```

In Terminal 1 (Core):
```
[Core] SetState: switch.pihole = off
```

### Test 3: Call a Service (Turn On)

```bash
grpcurl -plaintext -d '{
  "integration_id": "pihole_poc",
  "entity_id": "switch.pihole",
  "service": "turn_on"
}' localhost:50052 integration.IntegrationService/CallService
```

Verify Pi-hole is enabled and Core receives `switch.pihole = on`.

### Test 4: Query State from Core

```bash
grpcurl -plaintext -d '{
  "entity_id": "sensor.pihole_ads_blocked_today"
}' localhost:50051 core.CoreService/GetState
```

Expected output:
```json
{
  "found": true,
  "entityId": "sensor.pihole_ads_blocked_today",
  "state": "1234",
  "attributes": {
    "domains_blocked": "50000",
    "queries_today": "5000"
  },
  "lastUpdated": "1234567890"
}
```

## Success Criteria

✅ Core receives state updates from Integration every 30s
✅ States are stored correctly in Core's StateStore
✅ Service calls (turn_on/turn_off) work via gRPC
✅ Pi-hole actually enables/disables when commanded
✅ Business logic (`PiHoleIntegration`) has no HA dependencies
✅ The same `PiHoleIntegration` code could be used in LOCAL mode

## Flow Diagrams

### Startup Flow

```
User starts Core
    ↓
Core starts gRPC server (port 50051)
    ↓
Core waits for connections

User starts Integration with --host and --api-key
    ↓
Integration starts gRPC server (port 50052)
    ↓
Integration calls self.Initialize(config) via gRPC
    ↓
Integration creates PiHoleIntegration instance
    ↓
Integration.async_initialize() → connects to Pi-hole
    ↓
Integration returns entity_ids
    ↓
Integration calls self.Start() via gRPC
    ↓
Integration starts async update loop (30s)
```

### State Update Flow

```
[Every 30 seconds]

Integration: async_update()
    ↓
Integration: Call Pi-hole API
    ↓
Integration: Parse response, update entities_state
    ↓
Integration: For each entity_id:
    ↓
    HassProxy.states.async_set(entity_id, state, attributes)
        ↓
        gRPC call → Core.SetState(entity_id, state, attributes)
            ↓
            Core: StateStore.set_state(...)
            ↓
            Core: Log state update
```

### Service Call Flow

```
User: grpcurl CallService(turn_off)
    ↓
Integration gRPC Server: CallService()
    ↓
Integration: Check service name
    ↓
Integration: Call async_turn_off()
    ↓
Integration: Pi-hole API disable()
    ↓
Integration: async_update() (immediate refresh)
    ↓
Integration: HassProxy.states.async_set(switch.pihole, "off", {})
    ↓
    gRPC call → Core.SetState(switch.pihole, "off", {})
        ↓
        Core: StateStore.set_state(...)
        ↓
        Core: Log state update
```

## Observations & Learnings

After running the POC, document:

1. **Performance**: Measure gRPC call latency (SetState, CallService)
2. **Reliability**: Any connection issues or errors?
3. **Developer Experience**: How easy was it to write business logic without HA?
4. **Code Reusability**: Could `PiHoleIntegration` be used in LOCAL mode as-is?
5. **Debugging**: Was it easy to debug with grpcurl and logs?

## Next Steps (After POC Success)

### Phase 1: ProcessExecutor + Full Core API

1. Implement ProcessExecutor to auto-launch integration subprocess
2. Add Event bus to Core gRPC server
3. Add Service registry to Core gRPC server
4. Add Config API to Core gRPC server
5. Create RuntimeFactory in `__init__.py` to choose LOCAL vs REMOTE
6. Implement config flow for runtime mode selection

### Phase 2: DockerExecutor

1. Write Dockerfile for pi_hole integration
2. Implement DockerExecutor
3. Test resource limits

### Phase 3: Production Readiness

1. Error handling and retry logic
2. Reconnection on gRPC failure
3. Health checks
4. Comprehensive tests
5. Documentation

## Troubleshooting

**Problem:** `ModuleNotFoundError: No module named 'core_pb2'`
- **Solution:** Make sure you ran `protoc` to generate Python code from `.proto` files

**Problem:** Integration can't connect to Pi-hole
- **Solution:** Check Pi-hole IP address, verify API key, ensure Pi-hole is reachable

**Problem:** Core doesn't receive state updates
- **Solution:** Check that both gRPC servers are running, verify ports (50051, 50052)

**Problem:** grpcurl command not found
- **Solution:** Install grpcurl: `brew install grpcurl` (macOS) or download from GitHub

**Problem:** Integration crashes on update
- **Solution:** Check Pi-hole API compatibility, verify `hole` library version

## Summary

This POC proves that:

1. ✅ Integrations can run in separate processes
2. ✅ Bidirectional gRPC communication works (Core ↔ Integration)
3. ✅ Business logic can be isolated from Home Assistant
4. ✅ State synchronization works reliably
5. ✅ Service calls (commands) work via gRPC

**The concept is validated. We can proceed with full implementation.**

---

## Phase 0: Results

> **Status: ✅ COMPLETED**

### Implemented Components

#### 1. Core gRPC Server (`homeassistant/core_grpc/`) — starts automatically on HA boot

| Method | Status |
|---|---|
| `SetState(entity_id, state, attributes, entry_id)` | ✅ |
| `GetState(entity_id)` | ✅ |
| `RegisterService(domain, service, entry_id)` | ✅ |
| `RegisterWorker(entry_id, worker_address)` | ✅ |
| `CallServiceOnRemote` (stub) | ✅ |

#### 2. Worker gRPC Server (`homeassistant/core_grpc/worker_server.py`)

| Method | Status |
|---|---|
| `CallService(domain, service, entity_id, service_data)` | ✅ |

#### 3. Worker Client (`homeassistant/core_grpc/worker_client.py`)

| Component | Status |
|---|---|
| Connection from Core to worker | ✅ |

#### 4. `HomeAssistantGrpcProxy` (`homeassistant/worker/proxy.py`)

| Feature | Status |
|---|---|
| `hass.states.async_set` → gRPC SetState | ✅ |
| `hass.services.async_register` → gRPC RegisterService | ✅ |
| `hass.config_entries.async_forward_entry_setups` → dynamic platform setup | ✅ |
| Monkey-patches: `er.async_migrate_entries`, `async_get_clientsession` | ✅ |
| `async_run_hass_job`, `async_add_executor_job`, `async_create_task` | ✅ |

#### 5. ProcessExecutor (`homeassistant/executors/process.py`) *(removed — replaced by worker registry)*

| Component | Status |
|---|---|
| Subprocess launch | ✅ |

#### 6. Remote entry point Pi-hole (`homeassistant/components/pi_hole/remote/main.py`) *(removed — replaced by generic worker)*

| Component | Status |
|---|---|
| Pi-hole specific entry point | ✅ |

#### 7. Service routing Core → Worker

| Feature | Status |
|---|---|
| `RegisterService` installs a handler in `hass.services` that routes to the worker | ✅ |
| `switch.turn_on` / `switch.turn_off` routed to the worker | ✅ |
| `EVENT_SERVICE_REGISTERED` listener to reinstall the handler if HA overwrites it | ✅ |

### What Works End-to-End

- Pi-hole runs in a separate subprocess
- States are pushed every 10s via gRPC → visible in HA Developer Tools
- `switch.turn_on` from the HA UI → gRPC → worker → `api.enable()` → Pi-hole enabled ✅
- `switch.turn_off` from the HA UI → gRPC → worker → `api.disable()` → Pi-hole disabled ✅

---

## Phase 1: Generic Worker

> **Status: ✅ COMPLETED**

### Objective

Replace the specific `pi_hole/remote/main.py` with a **generic worker** capable of loading any integration without modifying its code.

### Guiding Principles

- The worker receives `[(domain, entry_id)]` and contacts the Core to obtain the config via gRPC `GetEntry(entry_id)`
- `HomeAssistantGrpcProxy` is enriched with the missing shims (device registry, dispatcher, timers, storage)
- Integrations **do not know** they are running in a remote worker
- Config flows stay in the Core (unchanged) — handled in a future phase

### New Components to Implement

#### 1. Generic worker (`homeassistant/worker/main.py`)

- Receives `entry_id` as argument
- Calls `GetEntry(entry_id)` → receives `{domain, config, options}`
- Dynamically loads `homeassistant.components.{domain}`
- Calls `async_setup_entry(hass_proxy, entry)`
- Supports N integrations in the same process

#### 2. New proto: `GetEntry`

```protobuf
rpc GetEntry(GetEntryRequest) returns (GetEntryResponse);

message GetEntryRequest {
  string entry_id = 1;
}

message GetEntryResponse {
  string domain  = 1;
  bytes  config  = 2;  // JSON serialized
  bytes  options = 3;  // JSON serialized
}
```

#### 3. Missing shims in `HomeAssistantGrpcProxy`

| Shim | Description |
|---|---|
| `_MockDeviceRegistry` | `async_get_or_create()`, `async_get()` |
| `_MockEntityRegistry` | `async_entries_for_config_entry()` → `[]` (complete) |
| `_MockIssueRegistry` | no-op |
| Local dispatcher | `async_dispatcher_connect` / `async_dispatcher_send` |
| `async_track_time_interval` | asyncio wrapper |
| `async_call_later` | `loop.call_later` |
| `helpers.storage.Store` | mock |

#### 4. RuntimeFactory in integrations

- LOCAL vs REMOTE logic in `async_setup_entry`
- Triggering the `ProcessExecutor` in REMOTE mode

#### 5. Improved ProcessExecutor

- Launches the generic worker instead of a specific entry point
- Passes only `entry_id` + `core_address`

### Target Flow (Phase 1)

```
HA Core boots
  └─► ProcessExecutor.start(entry_id="pihole_xxx", core="localhost:50051")
        └─► worker/main.py --entry-id pihole_xxx --core localhost:50051
              ├─► gRPC GetEntry("pihole_xxx")
              │     └─► Core responds: {domain="pi_hole", config={...}, options={...}}
              ├─► import homeassistant.components.pi_hole
              ├─► async_setup_entry(hass_proxy, entry)
              └─► WorkerGrpcServer.start(port=50052)
                    └─► RegisterWorker("pihole_xxx", "localhost:50052") → Core
```

### What Remains Out of Scope for Phase 1

| Feature | Planned Phase |
|---|---|
| Interactive config flow on worker side | Future phase |
| DockerExecutor / KubernetesExecutor | Phase 2+ |
| Worker management UI | Future phase |
| Automatic reconnection | Future phase |
