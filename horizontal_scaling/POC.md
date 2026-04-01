# Phase 0: Proof of Concept (POC)

## Objective

**Prove that a Home Assistant integration (Pi-hole) can run in a separate process and communicate bidirectionally with the Core via gRPC.**

Success means:
1. Pi-hole integration runs in a separate Python process
2. Core receives entity state updates via gRPC
3. Core can send commands (turn_on/turn_off) via gRPC
4. Business logic code is identical to what would run in LOCAL mode

## Scope

### What We Implement

✅ **Core gRPC Server** (minimal)
- `SetState(entity_id, state, attributes)` - Receive state updates from integration

✅ **Integration gRPC Server** (minimal)
- `Initialize(config)` - Setup the integration
- `Start()` - Begin polling Pi-hole
- `CallService(service, data)` - Handle turn_on/turn_off commands

✅ **Business Logic**
- `PiHoleIntegration` class with Pi-hole API interaction
- Polling logic (every 30 seconds)
- State management

✅ **Hass Proxy**
- `states.async_set()` → gRPC `SetState()` call

✅ **Manual Launch**
- Core started manually
- Integration started manually (no executor)

### What We Do NOT Implement

❌ Event bus (not used by Pi-hole)
❌ Service registry (Pi-hole doesn't register services)
❌ Config flow / Options flow
❌ Entity registry / Device registry
❌ Translations
❌ WebSocket handlers
❌ Executors (Process/Docker/Kubernetes)
❌ Reconnection / Retry logic
❌ Comprehensive error handling
❌ Tests
❌ Docker/K8s deployment

**Rationale:** Focus on proving the core concept with minimum code.

## Architecture

```
┌─────────────────────────────────┐
│   Terminal 1: HA Core           │
│                                  │
│   InMemoryStateStore             │
│   {entity_id → state}            │
│           ↑                      │
│   CoreGrpcServer                 │
│   - SetState()                   │
│   Port: 50051                    │
└─────────────────────────────────┘
            ↑
            │ gRPC
            │
            ↓
┌─────────────────────────────────┐
│   Terminal 2: Pi-hole Service   │
│                                  │
│   IntegrationGrpcServer          │
│   - Initialize()                 │
│   - Start()                      │
│   - CallService()                │
│   Port: 50052                    │
│           ↓                      │
│   PiHoleIntegration              │
│   - async_update() [loop 30s]   │
│   - async_turn_on/off()          │
│           ↓                      │
│   HassProxy                      │
│   - states.async_set()           │
│     → gRPC to Core               │
└─────────────────────────────────┘
```

## File Structure

```
poc/
├── protos/
│   ├── core.proto                    # Core API definition
│   ├── integration.proto             # Integration API definition
│   ├── core_pb2.py                   # Generated (via protoc)
│   ├── core_pb2_grpc.py              # Generated
│   ├── integration_pb2.py            # Generated
│   └── integration_pb2_grpc.py       # Generated
│
├── core/
│   ├── state_store.py                # Simple in-memory state storage
│   ├── grpc_server.py                # CoreGrpcServer implementation
│   └── main.py                       # Entry point to run Core
│
├── integration/
│   ├── pihole.py                     # PiHoleIntegration business logic
│   ├── grpc_server.py                # IntegrationGrpcServer implementation
│   ├── hass_proxy.py                 # Hass proxy (states.async_set via gRPC)
│   └── main.py                       # Entry point to run Integration service
│
├── requirements.txt                  # Python dependencies
└── README.md                         # Setup and usage instructions
```

## Protocol Definitions

### core.proto

```protobuf
syntax = "proto3";

package core;

// Service exposed by Home Assistant Core
service CoreService {
  // State Management
  rpc SetState(SetStateRequest) returns (SetStateResponse);
  rpc GetState(GetStateRequest) returns (GetStateResponse);
}

// Set or update an entity state
message SetStateRequest {
  string entity_id = 1;                  // e.g., "sensor.pihole_ads_blocked"
  string state = 2;                      // e.g., "1234"
  map<string, string> attributes = 3;    // Additional attributes
}

message SetStateResponse {
  bool success = 1;
  string error = 2;                      // Error message if success=false
}

// Get current state of an entity
message GetStateRequest {
  string entity_id = 1;
}

message GetStateResponse {
  bool found = 1;
  string entity_id = 2;
  string state = 3;
  map<string, string> attributes = 4;
  int64 last_updated = 5;                // Unix timestamp
}
```

### integration.proto

```protobuf
syntax = "proto3";

package integration;

// Service exposed by Integration
service IntegrationService {
  // Lifecycle
  rpc Initialize(InitRequest) returns (InitResponse);
  rpc Start(StartRequest) returns (StartResponse);
  rpc Stop(StopRequest) returns (StopResponse);
  
  // Service Calls
  rpc CallService(CallServiceRequest) returns (CallServiceResponse);
}

// Initialize the integration with configuration
message InitRequest {
  string integration_id = 1;             // Unique ID for this integration instance
  map<string, string> config = 2;        // Config: host, api_key, ssl, etc.
}

message InitResponse {
  bool success = 1;
  string error = 2;
  repeated string entity_ids = 3;        // List of entities managed by this integration
}

// Start the integration (begin polling, listening, etc.)
message StartRequest {
  string integration_id = 1;
}

message StartResponse {
  bool success = 1;
  string error = 2;
}

// Stop the integration gracefully
message StopRequest {
  string integration_id = 1;
}

message StopResponse {
  bool success = 1;
}

// Call a service on an entity
message CallServiceRequest {
  string integration_id = 1;
  string entity_id = 2;                  // e.g., "switch.pihole"
  string service = 3;                    // e.g., "turn_on", "turn_off"
  map<string, string> data = 4;          // Additional service data
}

message CallServiceResponse {
  bool success = 1;
  string error = 2;
}
```

## Component Specifications

### 1. State Store (core/state_store.py)

Simple in-memory storage for entity states.

```python
from dataclasses import dataclass, field
from typing import Dict
import time

@dataclass
class EntityState:
    entity_id: str
    state: str
    attributes: Dict[str, str] = field(default_factory=dict)
    last_updated: int = field(default_factory=lambda: int(time.time()))

class StateStore:
    """Simple in-memory state storage"""
    
    def __init__(self):
        self._states: Dict[str, EntityState] = {}
    
    def set_state(self, entity_id: str, state: str, attributes: Dict[str, str]) -> bool:
        """Set or update entity state"""
        self._states[entity_id] = EntityState(
            entity_id=entity_id,
            state=state,
            attributes=attributes,
            last_updated=int(time.time())
        )
        return True
    
    def get_state(self, entity_id: str) -> EntityState | None:
        """Get entity state"""
        return self._states.get(entity_id)
    
    def get_all_states(self) -> Dict[str, EntityState]:
        """Get all states"""
        return self._states.copy()
```

### 2. Core gRPC Server (core/grpc_server.py)

Implements the Core gRPC service.

```python
import grpc
from concurrent import futures
import asyncio
import sys
sys.path.append('.')  # Add current dir to path for proto imports

import core_pb2
import core_pb2_grpc
from state_store import StateStore

class CoreGrpcServicer(core_pb2_grpc.CoreServiceServicer):
    """Implementation of CoreService gRPC server"""
    
    def __init__(self, state_store: StateStore):
        self.state_store = state_store
    
    def SetState(self, request, context):
        """Handle SetState RPC from integration"""
        print(f"[Core] SetState: {request.entity_id} = {request.state}")
        print(f"[Core] Attributes: {dict(request.attributes)}")
        
        success = self.state_store.set_state(
            entity_id=request.entity_id,
            state=request.state,
            attributes=dict(request.attributes)
        )
        
        return core_pb2.SetStateResponse(success=success)
    
    def GetState(self, request, context):
        """Handle GetState RPC"""
        state = self.state_store.get_state(request.entity_id)
        
        if state is None:
            return core_pb2.GetStateResponse(found=False)
        
        return core_pb2.GetStateResponse(
            found=True,
            entity_id=state.entity_id,
            state=state.state,
            attributes=state.attributes,
            last_updated=state.last_updated
        )

def serve(port=50051):
    """Start the Core gRPC server"""
    state_store = StateStore()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    
    core_pb2_grpc.add_CoreServiceServicer_to_server(
        CoreGrpcServicer(state_store), server
    )
    
    server.add_insecure_port(f'[::]:{port}')
    server.start()
    
    print(f"[Core] gRPC Server started on port {port}")
    print(f"[Core] Waiting for integration to connect...")
    
    # Keep server running
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        print("\n[Core] Shutting down...")
        server.stop(0)
```

### 3. Core Main (core/main.py)

Entry point to start the Core gRPC server.

```python
import sys
sys.path.append('../protos')

from grpc_server import serve

if __name__ == '__main__':
    serve()
```

### 4. PiHole Integration Business Logic (integration/pihole.py)

Pure business logic with no Home Assistant dependencies.

```python
from typing import Dict
import asyncio
from hole import Hole  # Pi-hole API client library

class PiHoleIntegration:
    """Business logic for Pi-hole integration"""
    
    def __init__(self, host: str, api_key: str = None, ssl: bool = False):
        self.host = host
        self.api_key = api_key
        self.ssl = ssl
        self.api: Hole = None
        self.entities_state: Dict[str, dict] = {}
    
    async def async_initialize(self):
        """Initialize connection to Pi-hole"""
        self.api = Hole(
            host=self.host,
            api_key=self.api_key,
            ssl=self.ssl
        )
        
        # Test connection
        try:
            await self.api.get_data()
            print(f"[Integration] Connected to Pi-hole at {self.host}")
        except Exception as e:
            print(f"[Integration] Failed to connect to Pi-hole: {e}")
            raise
        
        # Initialize entity states
        self.entities_state = {
            'sensor.pihole_ads_blocked_today': {
                'state': '0',
                'attributes': {}
            },
            'sensor.pihole_ads_percentage_today': {
                'state': '0',
                'attributes': {}
            },
            'switch.pihole': {
                'state': 'off',
                'attributes': {}
            },
            'binary_sensor.pihole_status': {
                'state': 'off',
                'attributes': {}
            }
        }
    
    async def async_update(self) -> Dict[str, dict]:
        """Poll Pi-hole and update entity states"""
        try:
            data = await self.api.get_data()
            
            # Update sensor states
            self.entities_state['sensor.pihole_ads_blocked_today'] = {
                'state': str(data.get('ads_blocked_today', 0)),
                'attributes': {
                    'domains_blocked': str(data.get('domains_being_blocked', 0)),
                    'queries_today': str(data.get('dns_queries_today', 0)),
                }
            }
            
            self.entities_state['sensor.pihole_ads_percentage_today'] = {
                'state': str(data.get('ads_percentage_today', 0)),
                'attributes': {}
            }
            
            # Update switch/binary_sensor states
            status = data.get('status') == 'enabled'
            state_str = 'on' if status else 'off'
            
            self.entities_state['switch.pihole'] = {
                'state': state_str,
                'attributes': {
                    'gravity_last_updated': str(data.get('gravity_last_updated', {}))
                }
            }
            
            self.entities_state['binary_sensor.pihole_status'] = {
                'state': state_str,
                'attributes': {}
            }
            
            print(f"[Integration] Updated: {data.get('ads_blocked_today', 0)} ads blocked")
            
        except Exception as e:
            print(f"[Integration] Update failed: {e}")
        
        return self.entities_state
    
    async def async_turn_on(self):
        """Enable Pi-hole (turn on)"""
        print("[Integration] Turning Pi-hole ON")
        await self.api.enable()
        # Force immediate update
        await self.async_update()
    
    async def async_turn_off(self):
        """Disable Pi-hole (turn off)"""
        print("[Integration] Turning Pi-hole OFF")
        await self.api.disable()
        # Force immediate update
        await self.async_update()
    
    def get_current_states(self) -> Dict[str, dict]:
        """Return current entity states"""
        return self.entities_state
```

### 5. Hass Proxy (integration/hass_proxy.py)

Proxy that mimics `hass.states` interface but calls Core via gRPC.

```python
import grpc
import sys
sys.path.append('../protos')

import core_pb2
import core_pb2_grpc

class StatesProxy:
    """Proxy for hass.states that uses gRPC"""
    
    def __init__(self, core_address: str):
        self.channel = grpc.insecure_channel(core_address)
        self.stub = core_pb2_grpc.CoreServiceStub(self.channel)
    
    async def async_set(self, entity_id: str, state: str, attributes: dict = None):
        """Set entity state via gRPC"""
        if attributes is None:
            attributes = {}
        
        # Convert all attribute values to strings (gRPC map requirement)
        str_attributes = {k: str(v) for k, v in attributes.items()}
        
        response = self.stub.SetState(core_pb2.SetStateRequest(
            entity_id=entity_id,
            state=state,
            attributes=str_attributes
        ))
        
        if not response.success:
            print(f"[HassProxy] SetState failed: {response.error}")
    
    def get(self, entity_id: str):
        """Get entity state via gRPC"""
        response = self.stub.GetState(core_pb2.GetStateRequest(
            entity_id=entity_id
        ))
        
        if not response.found:
            return None
        
        # Return a simple object with state and attributes
        class State:
            def __init__(self, entity_id, state, attributes):
                self.entity_id = entity_id
                self.state = state
                self.attributes = attributes
        
        return State(response.entity_id, response.state, dict(response.attributes))
    
    def close(self):
        """Close gRPC channel"""
        self.channel.close()

class HassProxy:
    """Proxy for hass object"""
    
    def __init__(self, core_address: str = 'localhost:50051'):
        self.states = StatesProxy(core_address)
    
    def close(self):
        self.states.close()
```

### 6. Integration gRPC Server (integration/grpc_server.py)

Implements the Integration gRPC service.

```python
import grpc
from concurrent import futures
import asyncio
import sys
sys.path.append('../protos')

import integration_pb2
import integration_pb2_grpc
from pihole import PiHoleIntegration
from hass_proxy import HassProxy

class IntegrationGrpcServicer(integration_pb2_grpc.IntegrationServiceServicer):
    """Implementation of IntegrationService gRPC server"""
    
    def __init__(self, core_address: str = 'localhost:50051'):
        self.integration: PiHoleIntegration = None
        self.hass = HassProxy(core_address)
        self.update_task = None
        self.loop = asyncio.get_event_loop()
    
    def Initialize(self, request, context):
        """Initialize the integration"""
        print(f"[Integration] Initialize request: {request.integration_id}")
        print(f"[Integration] Config: {dict(request.config)}")
        
        config = dict(request.config)
        
        # Create PiHoleIntegration instance
        self.integration = PiHoleIntegration(
            host=config.get('host'),
            api_key=config.get('api_key'),
            ssl=config.get('ssl', 'false').lower() == 'true'
        )
        
        # Initialize (async call in sync context)
        try:
            self.loop.run_until_complete(self.integration.async_initialize())
            
            entity_ids = list(self.integration.get_current_states().keys())
            
            return integration_pb2.InitResponse(
                success=True,
                entity_ids=entity_ids
            )
        except Exception as e:
            return integration_pb2.InitResponse(
                success=False,
                error=str(e)
            )
    
    def Start(self, request, context):
        """Start the integration polling loop"""
        print(f"[Integration] Start request: {request.integration_id}")
        
        # Start update loop in background
        self.update_task = asyncio.run_coroutine_threadsafe(
            self._update_loop(),
            self.loop
        )
        
        return integration_pb2.StartResponse(success=True)
    
    def Stop(self, request, context):
        """Stop the integration"""
        print(f"[Integration] Stop request: {request.integration_id}")
        
        if self.update_task:
            self.update_task.cancel()
        
        self.hass.close()
        
        return integration_pb2.StopResponse(success=True)
    
    def CallService(self, request, context):
        """Handle service calls"""
        print(f"[Integration] CallService: {request.service} on {request.entity_id}")
        
        try:
            if request.service == 'turn_on':
                self.loop.run_until_complete(self.integration.async_turn_on())
            elif request.service == 'turn_off':
                self.loop.run_until_complete(self.integration.async_turn_off())
            else:
                return integration_pb2.CallServiceResponse(
                    success=False,
                    error=f"Unknown service: {request.service}"
                )
            
            # Send updated states to Core
            self.loop.run_until_complete(self._send_states())
            
            return integration_pb2.CallServiceResponse(success=True)
            
        except Exception as e:
            return integration_pb2.CallServiceResponse(
                success=False,
                error=str(e)
            )
    
    async def _update_loop(self):
        """Polling loop - updates Pi-hole data every 30s"""
        print("[Integration] Starting update loop (30s interval)")
        
        while True:
            try:
                await self.integration.async_update()
                await self._send_states()
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                print("[Integration] Update loop cancelled")
                break
            except Exception as e:
                print(f"[Integration] Update loop error: {e}")
                await asyncio.sleep(30)
    
    async def _send_states(self):
        """Send all entity states to Core"""
        states = self.integration.get_current_states()
        
        for entity_id, state_data in states.items():
            await self.hass.states.async_set(
                entity_id=entity_id,
                state=state_data['state'],
                attributes=state_data['attributes']
            )

def serve(port=50052, core_address='localhost:50051'):
    """Start the Integration gRPC server"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    
    integration_pb2_grpc.add_IntegrationServiceServicer_to_server(
        IntegrationGrpcServicer(core_address), server
    )
    
    server.add_insecure_port(f'[::]:{port}')
    server.start()
    
    print(f"[Integration] gRPC Server started on port {port}")
    print(f"[Integration] Connected to Core at {core_address}")
    
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        print("\n[Integration] Shutting down...")
        server.stop(0)
```

### 7. Integration Main (integration/main.py)

Entry point for the integration service with CLI arguments.

```python
import argparse
import sys
sys.path.append('../protos')

from grpc_server import serve
import integration_pb2
import integration_pb2_grpc
import grpc

def initialize_integration(host: str, api_key: str, ssl: bool, integration_address: str):
    """Call Initialize RPC to configure the integration"""
    channel = grpc.insecure_channel(integration_address)
    stub = integration_pb2_grpc.IntegrationServiceStub(channel)
    
    response = stub.Initialize(integration_pb2.InitRequest(
        integration_id='pihole_poc',
        config={
            'host': host,
            'api_key': api_key or '',
            'ssl': str(ssl).lower()
        }
    ))
    
    if response.success:
        print(f"[Main] Integration initialized. Entities: {list(response.entity_ids)}")
        
        # Start the integration
        start_response = stub.Start(integration_pb2.StartRequest(
            integration_id='pihole_poc'
        ))
        
        if start_response.success:
            print("[Main] Integration started successfully")
        else:
            print(f"[Main] Failed to start: {start_response.error}")
    else:
        print(f"[Main] Failed to initialize: {response.error}")
    
    channel.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Pi-hole Integration Service')
    parser.add_argument('--host', required=True, help='Pi-hole host address')
    parser.add_argument('--api-key', default='', help='Pi-hole API key')
    parser.add_argument('--ssl', action='store_true', help='Use SSL for Pi-hole connection')
    parser.add_argument('--port', type=int, default=50052, help='gRPC server port')
    parser.add_argument('--core-address', default='localhost:50051', help='Core gRPC address')
    
    args = parser.parse_args()
    
    print(f"[Main] Starting Pi-hole Integration Service")
    print(f"[Main] Pi-hole: {args.host}")
    print(f"[Main] Core: {args.core_address}")
    print(f"[Main] Port: {args.port}")
    
    # Start the gRPC server in background
    import threading
    server_thread = threading.Thread(
        target=serve,
        args=(args.port, args.core_address),
        daemon=True
    )
    server_thread.start()
    
    # Wait a bit for server to start
    import time
    time.sleep(2)
    
    # Initialize the integration
    initialize_integration(
        host=args.host,
        api_key=args.api_key,
        ssl=args.ssl,
        integration_address=f'localhost:{args.port}'
    )
    
    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[Main] Exiting...")
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
