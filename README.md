# solis-kde-plasma-sensors

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12+-blue?logo=python)](https://python.org)
[![Platform](https://img.shields.io/badge/platform-Fedora%2040%2B%20%7C%20KDE%20Plasma%206-blue?logo=fedora)](https://fedoraproject.org)
[![GitHub last commit](https://img.shields.io/github/last-commit/farazmazhar/solis-kde-plasma-sensors?logo=github)](https://github.com/farazmazhar/solis-kde-plasma-sensors)
[![GitHub release](https://img.shields.io/github/v/release/farazmazhar/solis-kde-plasma-sensors?include_prereleases&logo=github)](https://github.com/farazmazhar/solis-kde-plasma-sensors/releases)
[![SolisCloud](https://img.shields.io/badge/API-SolisCloud-orange)](https://www.soliscloud.com)

Solis inverter real-time data as native KDE System Monitor sensors.

Fetches data from the SolisCloud REST API (`inverterDetail` endpoint) and
exposes it through the
[ksystemstats_custom_sensors](https://github.com/vazh2100/ksystemstats_custom_sensors)
plugin.  No Home Assistant, no dashboard, no web UI — just your inverter data
right alongside CPU/network sensors in Plasma System Monitor.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                    SOLIS INVERTER (Hardware)                     │
│  S6-EH1P6K-L-PRO  ──  Solar Panels  ──  Battery  ──  Grid        │
└──────────────────────┬───────────────────────────────────────────┘
                       │ 5 min poll (HTTPS :13333)
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│                  SolisCloud REST API (cloud)                     │
│  POST /v1/api/inverterDetail   (HMAC-SHA1 signed)                │
│  Returns: pac, psumOrgin, familyLoadPowerOrigin, backupPowerA,   │
│           batteryCapacitySoc, eToday, allEnergyOriginal, ...     │
└──────────────────────┬───────────────────────────────────────────┘
                       │ JSON response
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  ~/.config/solis2kde/config.yaml   ◄──  api_key / api_secret     │
│                                                                  │
│  ~/.local/bin/solis2kde.py  ─── systemd user service ──────────  │
│  │  (solis2kde.service, Wants=network-online.target)             │
│  │  Restart=on-failure, RestartSec=30                            │
│  │                                                               │
│  │  Reads config, signs requests, parses JSON,                   │
│  │  writes values atomically (.tmp + rename)                     │
│  └──  Writes 9 files to /tmp/:                                   │
│                                                                  │
│  /tmp/solis_solar_w.txt         (W)                              │
│  /tmp/solis_grid_w.txt          (W)  Grid Import                 │
│  /tmp/solis_grid_import_w.txt   (W)  Grid Load                   │
│  /tmp/solis_load_w.txt          (W)  Total consumption           │
│  /tmp/solis_backup_w.txt        (W)  Essential circuits          │
│  /tmp/solis_smart_w.txt         (W)  Non-essential (computed)    │
│  /tmp/solis_battery_pct.txt     (%)                              │
│  /tmp/solis_etoday_kwh.txt      (kWh)                            │
│  /tmp/solis_etotal_kwh.txt      (kWh)                            │
└──────────────────────┬───────────────────────────────────────────┘
                       │ file read (poll during update())
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  /usr/lib64/qt6/plugins/ksystemstats/ksystemstats_plugin.so      │
│                                                                  │
│  ksystemstats_custom_sensors plugin (built from source)          │
│  Reads  ~/.config/customsensorrc  for sensor definitions         │
│  Reads  /tmp/solis_*.txt  for current values                     │
│  Exposes sensors to ksystemstats D-Bus interface                 │
└──────────────────────┬───────────────────────────────────────────┘
                       │ native KDE sensor protocol
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│              KDE Plasma 6 System Monitor (KSysGuard)             │
│                                                                  │
│  9 custom sensors appear alongside CPU/RAM/Network sensors       │
│  Page → Edit → Add Sensor → "Custom Sensors" container           │
│  Gauges, time-series graphs, facelets — full native support      │
└──────────────────────────────────────────────────────────────────┘
```

### File Locations on Disk

```
~/.config/
├── solis2kde/config.yaml        API credentials (mode 0600)
├── customsensorrc               Plugin sensor definitions (mode 0600)
└── systemd/user/solis2kde.service  systemd user unit

~/.local/bin/solis2kde.py        Python daemon (mode 755)

/usr/lib64/qt6/plugins/ksystemstats/
└── ksystemstats_plugin.so       KF6 System Stats plugin (built from source)

/tmp/solis_*.txt                 9 sensor value files (written atomically)
```

### Data Flow

```
solis2kde.py (daemon)                         ksystemstats (KDE daemon)
────────────────────────                      ────────────────────────
while True:                                   on update():
    sign request with HMAC-SHA1                   for each sensor:
    POST /v1/api/inverterDetail                       open file
    parse JSON                                        readLine()
    for each field in FIELD_MAP:                      parseFloat()
        write(tmp → rename to /tmp/solis_*)           setValue()
    sleep(poll_interval)                          emit change to D-Bus
```

## Sensors

| Sensor           | File                             | Unit  | Source                                |
|------------------|----------------------------------|-------|---------------------------------------|
| Solar Generation | `/tmp/solis_solar_w.txt`         | W     | `pac` × 1000 (API returns kW)        |
| Grid Import      | `/tmp/solis_grid_w.txt`          | W     | `psumOrgin` — total grid terminal     |
| Grid Load        | `/tmp/solis_grid_import_w.txt`   | W     | `acGridPowerA` — grid meter           |
| Load Consumption | `/tmp/solis_load_w.txt`          | W     | `familyLoadPowerOrigin`               |
| Backup Load      | `/tmp/solis_backup_w.txt`        | W     | `backupPowerA` — essential circuits   |
| Smart Load       | `/tmp/solis_smart_w.txt`         | W     | computed: total load − backup         |
| Battery SOC      | `/tmp/solis_battery_pct.txt`     | %     | `batteryCapacitySoc`                  |
| Energy Today     | `/tmp/solis_etoday_kwh.txt`      | kWh   | `eToday`                              |
| Total Energy     | `/tmp/solis_etotal_kwh.txt`      | kWh   | `allEnergyOriginal`                   |

## Setup

Run the install script:

```bash
bash install.sh
```

This will:
1. Install build dependencies (cmake, Qt6, KF6, lm_sensors devel).
2. Clone and build the `ksystemstats_custom_sensors` plugin from source.
3. Copy the built `.so` to the KDE plugin path.
4. Install `solis2kde.py` to `~/.local/bin/`.
5. Create `~/.config/customsensorrc` with all 9 sensor definitions.
6. Create `~/.config/solis2kde/config.yaml` with placeholder credentials.
7. Install and start the systemd user service.

### Post-install

1. **Add credentials** — edit `~/.config/solis2kde/config.yaml`:

   ```yaml
   api_key: "your_api_key"
   api_secret: "your_api_secret"
   station_id: "your_station_id"
   poll_interval: 300
   ```

   Get API credentials from [soliscloud.com](https://www.soliscloud.com/) →
   Personal → API Management.

2. **Test the connection**:

   ```bash
   python3 ~/.local/bin/solis2kde.py --discover
   ```

   This prints the full `inverterDetail` JSON so you can inspect field names.

3. **Open KDE System Monitor** → *Page* → *Edit* → *Add Sensor* → add the
   9 sensors.

## Rebuilding After Fedora Upgrade

Fedora major upgrades (e.g. 43 → 44) often ship newer KDE/Qt libraries.
The locally-built `ksystemstats_plugin.so` may fail to load because it was
linked against the old library versions.  Symptoms:

```
journalctl --user -u plasma-ksystemstats --no-pager | grep "plugin"
  → "Cannot load library ... version mismatch"
```

Fix — rebuild the plugin against the new libraries:

```bash
# 1. Reinstall devel packages (new versions from the upgrade)
sudo dnf reinstall -y cmake extra-cmake-modules qt6-qtbase-devel \
  kf6-kcoreaddons-devel libksysguard-devel lm_sensors-devel

# 2. Rebuild
cd /tmp/ksystemstats_custom_sensors
git pull --ff-only
mkdir -p build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release
cmake --build .

# 3. Install
sudo cp bin/ksystemstats_plugin.so /usr/lib64/qt6/plugins/ksystemstats/

# 4. Restart
systemctl --user restart plasma-ksystemstats.service
```

If the cloned source was deleted, re-clone:

```bash
git clone https://github.com/vazh2100/ksystemstats_custom_sensors.git \
  /tmp/ksystemstats_custom_sensors
```

## Adjusting Field Mappings

If `--discover` shows different JSON key names, edit `FIELD_MAP` at the top
of `~/.local/bin/solis2kde.py`:

```python
FIELD_MAP = {
    "solar_w": {
        "file": "/tmp/solis_solar_w.txt",
        "keys": ["pac"],              # API key name(s), case-insensitive
        "multiply": 1000,             # scale factor (kW → W)
    },
    "grid_w": {
        "file": "/tmp/solis_grid_w.txt",
        "keys": ["psumOrgin"],        # signed: +export, -import
    },
    "grid_import_w": {
        "file": "/tmp/solis_grid_import_w.txt",
        "keys": ["acGridPowerA"],     # negative = import → abs()
    },
    "load_w": {
        "file": "/tmp/solis_load_w.txt",
        "keys": ["familyLoadPowerOrigin", "totalLoadPower"],
    },
    "backup_w": {
        "file": "/tmp/solis_backup_w.txt",
        "keys": ["backupPowerA"],
    },
    "battery_pct": {
        "file": "/tmp/solis_battery_pct.txt",
        "keys": ["batteryCapacitySoc", "batteryPercent"],
    },
    "etoday_kwh": {
        "file": "/tmp/solis_etoday_kwh.txt",
        "keys": ["eToday"],
    },
    "etotal_kwh": {
        "file": "/tmp/solis_etotal_kwh.txt",
        "keys": ["allEnergyOriginal", "eTotal"],
    },
}
```

After editing, restart:

```bash
systemctl --user restart solis2kde.service
```

## Sensor Details

### Grid Import vs Grid Load

Both measure import power but at different points inside the inverter:

| Sensor       | API field      | What it measures                      |
|--------------|----------------|---------------------------------------|
| Grid Import  | `psumOrgin`    | Total power at the inverter's grid terminal |
| Grid Load    | `acGridPowerA` | Power at the internal grid meter       |

The values often differ (e.g. 845W vs 430W).  The gap may be inverter
self-consumption or internal power routing.

### Load Breakdown

- **Load Consumption** = total household load (`familyLoadPowerOrigin`)
- **Backup Load** = essential/backup circuits (`backupPowerA`)
- **Smart Load** = non-essential load (total − backup, computed)

So: `Load Consumption = Backup Load + Smart Load`.

### Grid Sign Convention

- **Positive** `psumOrgin` = exporting to grid (selling)
- **Negative** = importing from grid (buying)

The daemon writes the absolute value.  Direction is logged.

## Logs

```bash
journalctl --user -u solis2kde -f
```

## Troubleshooting

### Plugin not appearing / library errors

```bash
journalctl --user -u plasma-ksystemstats --no-pager | grep plugin
```

If you see version mismatch errors, rebuild per [Fedora upgrade](#rebuilding-after-fedora-upgrade) section.

### No sensor data

- Check daemon: `journalctl --user -u solis2kde -f`
- Verify temp files: `ls -la /tmp/solis_*.txt`
- Test API: `python3 ~/.local/bin/solis2kde.py --discover`

### Config secrets

`~/.config/solis2kde/config.yaml` and `~/.config/customsensorrc` are
created with mode `0600` (user-read-write only).

## Requirements

- **Fedora 40+** with KDE Plasma 6
- **Python 3** (stdlib only — no pip packages)
- Build deps installed automatically by `install.sh`
