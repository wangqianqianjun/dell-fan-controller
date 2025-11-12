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

## Safety

- Manual mode relies on Dell OEM IPMI commands: `0x30 0x30 0x01 0x00` (disable auto) and `0x30 0x30 0x02 0xff <pwm>` (set percentage).
- On shutdown (including crashes) an `0x30 0x30 0x01 0x01` command hands control back to iDRAC.
- You can return to automatic control at any moment by clicking “交还 iDRAC / Return to iDRAC” in the UI or via the API.
