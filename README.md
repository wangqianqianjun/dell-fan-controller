# Dell R730xd Fan Controller

[中文版说明](README.zh-CN.md)

Python service that exposes `http://<server-ip>:6180/dellfans` to monitor and control Dell PowerEdge R730xd fans. It uses raw IPMI commands (no `ipmitool` dependency) to:

- Read CPU1/CPU2, inlet, exhaust temperatures, and every fan RPM.
- Present a web dashboard with live metrics, errors, and mode status.
- Override minimum/maximum manual duty cycle limits and switch between manual/auto control.
- Return fan ownership to iDRAC automatically when the process exits.

> **Note**: the process must access `/dev/ipmi0`, so run it with `sudo` or grant the user the required permissions.

## Screenshot

![Web Dashboard](pic.png)

## Running

### Foreground (interactive)

```bash
cd /data/home/dylan/dellfans
sudo python3 main.py
```

Closing the terminal stops the service.

### Background via systemd

1. Install the service file and reload systemd:
   ```bash
   sudo cp /data/home/dylan/dellfans/dellfans.service /etc/systemd/system/
   sudo systemctl daemon-reload
   ```
2. Enable and start:
   ```bash
   sudo systemctl enable --now dellfans.service
   ```
3. Manage it:
   ```bash
   sudo systemctl status dellfans.service
   sudo journalctl -u dellfans.service -f
   sudo systemctl stop dellfans.service
   ```

The UI then becomes available at `http://<server-ip>:6180/dellfans`.

## Configuration

`config.json` (created in the working directory) controls defaults:

| Field | Description |
| --- | --- |
| `min_pwm` / `max_pwm` | Minimum/maximum duty cycle allowed in manual mode (0–100). |
| `default_pwm` | Duty cycle applied immediately after switching into manual mode. |
| `poll_interval_seconds` | Telemetry polling interval in seconds (min 0.5 s). |
| `listen_host` / `listen_port` | HTTP bind address (defaults to `0.0.0.0:6180`). |
| `transport` | `auto` (default), `kcs`, or `lan`. Use `lan` for iDRAC9/14G+. |
| `lan_host` / `lan_user` / `lan_password` | iDRAC network address and credentials for lanplus. Required when `transport` is `lan` or `auto` on iDRAC9+. |
| `lan_port` / `lan_privilege` | lanplus port (default 623) and privilege level (default `ADMIN`). |
| `use_bridge` / `bridge_channel` / `bridge_target` | Optional IPMB bridging settings (defaults: `false`, channel `6`, target `0x2c`). |

Manual min/max limits and the poll interval can also be changed from the web UI; updates are persisted back to `config.json`.

### Adjusting the telemetry poll interval

The default interval is `2.0` seconds. Change it at runtime via either method:

1. **Web UI** – On the `/dellfans` page, enter the new value (≥0.5 s, decimals allowed) in “Sensor refresh interval” and click “Save”.
2. **Command line** – Call the REST endpoint directly (example: set to 1.5 s):
   ```bash
   curl -X POST http://<server-ip>:6180/dellfans/api/poll_interval \
        -H 'Content-Type: application/json' \
        -d '{"seconds": 1.5}'
   ```
   Replace `<server-ip>` with the actual address (or `127.0.0.1`). The endpoint updates both the running poller thread and `config.json` without restarting systemd.

## Choosing the right transport (iDRAC generation)

### Older iDRAC (7/8, 11G/13G, e.g., R730xd)
- Uses host/KCS via `/dev/ipmi0`. No network auth required.
- Run as root (or with IPMI device permissions) and keep `transport: "kcs"` or `auto`.
- Manual mode commands work locally; no `ipmitool` dependency needed.

### Newer iDRAC (9, 14G/15G, e.g., R940/R740/R650/R750)
- Dell blocks the legacy fan override commands on the host channel. Use lanplus to the iDRAC.
- Install `ipmitool` and provide iDRAC credentials:
  ```bash
  export DELLFANS_IDRAC_HOST=<idrac-host-or-ip>
  export DELLFANS_IDRAC_USER=<user>
  export DELLFANS_IDRAC_PASSWORD=<password>
  # optional: export DELLFANS_IDRAC_PORT=623
  ```
  Or set `transport: "lan"` plus `lan_host`/`lan_user`/`lan_password` in `config.json`.
- Optional IPMB bridge (if your platform requires it): set `use_bridge: true` and adjust `bridge_channel`/`bridge_target` (defaults 6 / 0x2c) to match your BMC wiring.
- Restart the service. Startup prints the chosen transport; UI/API will surface any IPMI completion codes if the BMC rejects control.

## Safety

- Manual mode relies on Dell OEM IPMI commands: `0x30 0x30 0x01 0x00` (disable auto) and `0x30 0x30 0x02 0xff <pwm>` (set percentage).
- On shutdown (including crashes) an `0x30 0x30 0x01 0x01` command hands control back to iDRAC.
- You can return to automatic control at any moment by clicking “交还 iDRAC / Return to iDRAC” in the UI or via the API.
