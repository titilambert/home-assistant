# Home Assistant Remote Integration — Getting Started

This guide shows how to run a Home Assistant integration in **REMOTE mode**: the
integration logic runs in a separate process (or machine) and communicates with the
HA Core via gRPC.

Two integrations currently support REMOTE mode: **Pi-hole** and **Sun**.

---

## Architecture overview

```
┌──────────────────────────────────────┐        ┌────────────────────────────────┐
│         Home Assistant Core          │        │   Remote Integration Process   │
│                                      │        │                                │
│  remote_integration_server (gRPC)   │◄──────►│  IntegrationGrpcServer         │
│  - SetState / GetState              │        │  - polls Pi-hole / astral      │
│  - GetConfig                        │        │  - pushes states → Core        │
│  - GetIntegrationConfig             │        │  - handles turn_on / turn_off  │
│  - RegisterIntegration              │        │  - registers itself on startup │
└──────────────────────────────────────┘        └────────────────────────────────┘
```

### Startup sequence

1. HA starts → Core gRPC server binds on configured addresses (default: all interfaces, port 50051)
2. User configures the integration in **REMOTE mode** via the HA options flow
3. Remote process starts with only `--core-address`
4. Remote process calls `GetIntegrationConfig` → receives host, password, etc. from HA
5. Remote process calls `RegisterIntegration` → Core stores a gRPC stub back to the process
6. Remote process pushes entity states every 30 s via `SetState`
7. UI commands (turn on/off) → Core calls `CallService` on the remote process

---

## 1. configuration.yaml

Add the following section to restrict which network interfaces the Core gRPC server
listens on.  Without this section the server listens on **all interfaces** (`::`).

```yaml
# configuration.yaml
remote_integration_server:
  bindings:
    - "127.0.0.1"     # localhost only — remote process on the same machine
    - "192.168.4.5"   # LAN interface — remote process on another machine
  port: 50051         # optional, default: 50051
```

> **Security note (Phase 0):** the gRPC channel is unencrypted and unauthenticated.
> Bind only to trusted interfaces or use a firewall rule to restrict access to port 50051.
> mTLS and token authentication are planned for a later phase.

---

## 2. Enable REMOTE mode for an integration

In the HA UI:

1. Go to **Settings → Devices & Services**
2. Find the integration (Pi-hole or Sun)
3. Click **Configure** (options flow)
4. Set **Runtime mode** to `remote`
5. Save — HA reloads the entry in REMOTE mode

---

## 3. Start the remote process

The remote process only needs the address of the Core gRPC server.
All other configuration (host, password, location…) is fetched from HA automatically.

### Pi-hole

```bash
# Activate the HA virtualenv first
source /path/to/ha/core/.venv/bin/activate

python -m homeassistant.components.pi_hole.remote.main \
    --core-address 192.168.4.5:50051 \
    --port 50052              # port this process listens on (default: 50052)
```

### Sun

```bash
source /path/to/ha/core/.venv/bin/activate

python -m homeassistant.components.sun.remote.main \
    --core-address 192.168.4.5:50051 \
    --port 50053              # port this process listens on (default: 50053)
```

> The `--core-address` must be reachable from the machine running the remote process.
> The `--port` must be reachable **from the HA Core machine** (Core calls back on it for service calls).

---

## 4. Full example — Pi-hole on a separate machine

```
Machine A: Home Assistant Core  (192.168.4.5)
Machine B: Pi-hole process      (192.168.4.10)
```

**`configuration.yaml` on Machine A:**

```yaml
remote_integration_server:
  bindings:
    - "192.168.4.5"
  port: 50051
```

**Pi-hole integration options** (HA UI): `runtime_mode = remote`

**On Machine B:**

```bash
python -m homeassistant.components.pi_hole.remote.main \
    --core-address 192.168.4.5:50051 \
    --port 50052
```

Expected output on Machine B:
```
INFO  Initializing Pi-hole remote integration
INFO  Got config from Core: host=pihole.local entry_id=abc123
INFO  Registered with Core. Entities: ['sensor.pi_hole_abc123_ads_blocked_today', ..., 'switch.pi_hole_abc123']
INFO  Update loop started (30s interval)
INFO  Pushed states to Core (blocking=True)
```

Expected output in HA logs:
```
INFO  Core gRPC server binding on 192.168.4.5:50051
INFO  RegisterIntegration: domain=pi_hole id=abc123 address=192.168.4.10:50052
```

---

## 5. Full example — Sun on the same machine (localhost)

**`configuration.yaml`:**

```yaml
remote_integration_server:
  bindings:
    - "127.0.0.1"
  port: 50051
```

**Sun integration options** (HA UI): `runtime_mode = remote`

```bash
python -m homeassistant.components.sun.remote.main \
    --core-address 127.0.0.1:50051 \
    --port 50053
```

The process fetches latitude/longitude/timezone from HA via `GetConfig`, computes
solar position with `astral`, and pushes `sun.sun` + 8 sensor states on a schedule
that matches the built-in Sun entity update frequency.

---

## 6. Switching back to LOCAL mode

In the HA UI: **Settings → Devices & Services → Configure → Runtime mode → `local`**

The integration reloads immediately in standard local mode. The remote process can be stopped.

---

## Roadmap

| Phase | Feature |
|-------|---------|
| 0 ✅ | Bidirectional gRPC, Pi-hole + Sun REMOTE mode, config flow, `GetIntegrationConfig` |
| 1 | `ProcessExecutor` — HA auto-launches the remote process |
| 2 | `DockerExecutor` — integration runs in a container with resource limits |
| 3 | `KubernetesExecutor` — pod + service, auto-restart, rolling updates |
| 4 | mTLS + token auth, entity/device registry via gRPC, full Core API |
| 5 | Migration tooling, base classes, CLI scaffold, observability |
