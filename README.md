# Typhur Dome 2 — Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/skontzias/typhur-dome-2-ha.svg)](https://github.com/skontzias/typhur-dome-2-ha/releases)

> **This is not an official Typhur integration.**
> It is an independent, community-developed project with no affiliation with or endorsement from Typhur.
> It reverse-engineers the Typhur cloud API and **requires an active internet connection and Typhur's cloud services** — there is no local/LAN communication path.
> It may break if Typhur changes their backend without notice.

Connects the **Typhur Dome 2** air fryer to Home Assistant via cloud MQTT (AWS IoT), exposing real-time cooking state as sensor and binary sensor entities.

---

## Supported devices

| Device | Model code | Status |
|--------|-----------|--------|
| Typhur Dome 2 | AF04 | ✅ Confirmed working |
| Typhur Dome (original) | TBD | Likely compatible — untested |

Other Typhur devices (Sync thermometers, etc.) use the same cloud stack but are not yet exposed by this integration.

---

## Features

- **~2-second real-time updates** while cooking (cloud MQTT push)
- **Automatic REST fallback** polling when MQTT is unavailable
- **Auto-reconnect** with exponential back-off on connection drops
- **Token and certificate auto-refresh** — no manual intervention needed
- **Native temperature unit support** — HA converts °F ↔ °C based on your locale

### Exposed entities

#### Sensors

| Entity | Description | Unit |
|--------|-------------|------|
| `sensor.*_status` | Device status (`offline` / `standby` / `cooking` / `paused` / `done`) | — |
| `sensor.*_top_temperature` | Top heating element temperature | °F / °C |
| `sensor.*_bottom_temperature` | Bottom chamber temperature | °F / °C |
| `sensor.*_target_temperature` | Set target temperature | °F / °C |
| `sensor.*_remaining_time` | Seconds until cook completes | s |
| `sensor.*_elapsed_time` | Seconds elapsed in current cook | s |
| `sensor.*_total_cook_time` | Total set cook duration | s |
| `sensor.*_cooking_mode` | Active preset (Air Fry, Roast, Bake, …) | — |
| `sensor.*_cooking_state` | Cooking phase (`preheating` / `cooking` / `paused` / `done`) | — |
| `sensor.*_cooking_stage` | Stage progress for multi-stage cooks (e.g. `1/2`) | — |
| `sensor.*_preheat_remaining` | Seconds remaining in preheat phase | s |

#### Binary sensors

| Entity | `on` state | Device class |
|--------|-----------|--------------|
| `binary_sensor.*_cooking` | Device is actively cooking | `running` |
| `binary_sensor.*_basket` | Basket has been removed | `door` |
| `binary_sensor.*_error` | Device is reporting an error | `problem` |
| `binary_sensor.*_preheating` | Device is in preheat phase | — |

---

## Requirements

- **Typhur account** with the Dome 2 paired in the Typhur mobile app
- **Internet connectivity** from your Home Assistant host — cloud access is mandatory; there is no local mode
- Python packages `paho-mqtt>=1.6.1` and `cryptography>=38.0.0` (installed automatically by HA)

---

## Installation

### Via HACS (recommended)

1. Open HACS → **Integrations** → ⋮ → **Custom repositories**
2. Add `https://github.com/skontzias/typhur-dome-2-ha` with category **Integration**
3. Search for **Typhur Dome 2** and install
4. Restart Home Assistant

### Manual

1. Download the latest release
2. Copy the `custom_components/typhur/` directory into your HA `/config/custom_components/typhur/`
3. Restart Home Assistant

---

## Configuration

1. Go to **Settings → Devices & Services → Add integration**
2. Search for **Typhur Dome 2**
3. Enter your Typhur account credentials:
   - **Email** — the email address you registered with in the Typhur app
   - **Password** — your Typhur account password
   - **Region** — `us` for accounts created at typhur.com · `eu` for typhur.de
4. Click **Submit**

HA will log in, discover your devices, and start listening for updates. All entities appear under **Settings → Devices & Services → Typhur Dome 2**.

---

## How it works

### Authentication

Typhur uses a proprietary REST API hosted at `api.iot.typhur.com` (US) or `api.iot.typhur.de` (EU). Credentials are sent as email + MD5-hashed password. All requests are signed with an HMAC-like scheme using an application key extracted from the Typhur APK. A successful login returns a session token valid for approximately 24 hours; the integration refreshes it automatically.

### MQTT (primary data path)

After authentication the integration:
1. Calls `/app/device/bind/list` to discover paired devices and their MQTT topic paths (`device/{model}/{device_id}/pub`)
2. Calls `/app/mqtt/cert/apply` to obtain a time-limited PKCS#12 client certificate bundle from the Typhur CDN
3. Connects to the Typhur AWS IoT broker at `a2rac2pr1im2vr-ats.iot.us-west-2.amazonaws.com:8883` using mutual TLS
4. Subscribes to each device's pub topic and receives `AF04:status:report` messages approximately every 2 seconds while the device is cooking

### REST fallback

When MQTT is unavailable (network blip, cert expiry, broker maintenance) the integration automatically falls back to polling `/app/device/bind/list` every 30 seconds. The `lastStatusCmd` field in that response contains the last known device state.

### Connection lifecycle

```
HA startup
  │
  ├─ login() ──────────────────────── token (23hr TTL)
  ├─ get_devices()
  ├─ get_mqtt_certs() ─────────────── P12 cert (23hr TTL)
  └─ MQTT connect → subscribe
        │
        ├─ on_message → async_set_updated_data()   ← primary path
        │
        └─ on_disconnect
              │
              ├─ refresh certs
              ├─ reconnect (exponential back-off, max 5 min)
              └─ REST poll (fallback while reconnecting)
```

---

## Known limitations

### Read-only

This version is **read-only** — it does not support starting, stopping, pausing, or adjusting a cook via HA. Control support is planned for a future release.

### Cloud-dependent

**An active internet connection is required at all times.** The integration communicates exclusively through Typhur's cloud (REST + AWS IoT MQTT). If your HA host loses internet access or Typhur's servers are unreachable, entities will go unavailable. There is no local/LAN fallback.

### Inferred constant values

The following mappings were established from live captures of a Typhur Dome 2. They need validation across more cooking modes:

- **`cookingMode` integers 2–14** (Roast, Bake, Broil, etc.) — ordered to match the Dome 2 UI but not yet confirmed by live capture
- **`cookingState` values 1, 2, 5, 6** — inferred from context; only 0 (paused), 3 (cooking), and 4 (done) are confirmed

If you observe unexpected state labels, please [open an issue](https://github.com/skontzias/typhur-dome-2-ha/issues) with the raw MQTT payload from the probe script.

---

## Automations

### Notify when cook is done

```yaml
automation:
  - alias: "Typhur - Notify when cook is done"
    triggers:
      - trigger: state
        entity_id: sensor.typhur_dome_2_status
        to: "done"
    actions:
      - action: notify.mobile_app_your_phone
        data:
          title: "Air fryer done"
          message: "Your food is ready!"
```

### Alert if basket left open

```yaml
automation:
  - alias: "Typhur - Basket left open"
    triggers:
      - trigger: state
        entity_id: binary_sensor.typhur_dome_2_basket
        to: "on"
        for:
          minutes: 2
    conditions:
      - condition: state
        entity_id: binary_sensor.typhur_dome_2_cooking
        state: "off"
    actions:
      - action: notify.mobile_app_your_phone
        data:
          title: "Air fryer"
          message: "Basket has been out for 2 minutes."
```

---

## Troubleshooting

### Integration not loading

Check the HA logs for `[typhur]` entries. Common causes:
- **Invalid credentials** — re-enter your email and password in the integration settings
- **Wrong region** — US accounts must use `us`; EU accounts use `eu`
- **No internet / Typhur cloud down** — verify HA can reach `api.iot.typhur.com` (cloud access is required)

### Entities stuck on "Unavailable"

The Dome 2 reports `globalStatus: offline` when not powered on or not connected to Wi-Fi. Entities become unavailable in that state by design. Power on the device and verify it has a Wi-Fi connection in the Typhur app.

### Stale temperature values

If temperatures stop updating while cooking, MQTT may have disconnected. The integration reconnects automatically within 5–300 seconds (exponential back-off). Check **Settings → System → Logs** for `[typhur]` reconnect activity.

### Running the probe script

A standalone probe script (`typhur_probe.py`) is included for debugging and capturing raw device state. It requires `paho-mqtt` and `cryptography`:

```bash
pip install paho-mqtt cryptography
TYPHUR_EMAIL=you@example.com TYPHUR_PASS=yourpassword python3 typhur_probe.py
```

Captured messages are saved to `typhur_captured.json`.

---

## Contributing

Contributions are welcome — especially:
- Confirming `cookingMode` values for presets other than Air Fry
- Confirming `cookingState` values for paused/done/cooling states
- Testing with Typhur Dome (original) or other AF models

Please open an issue or PR with raw MQTT payload dumps from `typhur_captured.json`.

---

## Disclaimer

This project is not affiliated with, endorsed by, or supported by Typhur. Use at your own risk. The integration relies on undocumented, reverse-engineered APIs that may change at any time.

---

## License

MIT — see [LICENSE](LICENSE)
