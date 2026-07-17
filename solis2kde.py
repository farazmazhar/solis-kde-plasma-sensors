#!/usr/bin/env python3
"""solis2kde.py — SolisCloud inverter data fetcher for KDE System Monitor.

Reads config from ~/.config/solis2kde/config.yaml, polls the SolisCloud
REST API via inverterDetail, and writes numeric values to /tmp files
consumed by the ksystemstats_custom_sensors plugin.

API auth: HMAC-SHA1 signing per SolisCloud API doc V2.0.3.
Every request is signed with ``Authorization: API {apiId}:{sign}``.
"""

import base64
import email.utils
import hashlib
import hmac
import json
import logging
import os
import sys
import time
import urllib.request
import urllib.error

# ---------------------------------------------------------------------------
# Field map
#
# Each entry maps a metric to its output file and the API JSON keys to
# search for (case-insensitive, first match wins).
#
# "multiply" — scale factor applied to the raw value (e.g. API returns kW,
#   we want W, so multiply=1000).
#
# Edit ``keys`` here if your inverter uses different JSON field names.
# Run ``--discover`` to see the full response.
# ---------------------------------------------------------------------------
FIELD_MAP = {
    "solar_w": {
        "file": "/tmp/solis_solar_w.txt",
        "keys": ["pac"],
        "multiply": 1000,
        "description": "PV generation (kW from API, converted to W)",
    },
    "grid_w": {
        "file": "/tmp/solis_grid_w.txt",
        "keys": ["psumOrgin"],
        "multiply": 1,
        "description": "Grid power (W); positive=export, negative=import",
    },
    "load_w": {
        "file": "/tmp/solis_load_w.txt",
        "keys": ["familyLoadPowerOrigin", "totalLoadPower"],
        "multiply": 1,
        "description": "Household load (W)",
    },
    "battery_pct": {
        "file": "/tmp/solis_battery_pct.txt",
        "keys": ["batteryCapacitySoc", "batteryPercent"],
        "multiply": 1,
        "description": "Battery state of charge (%)",
    },
    "etoday_kwh": {
        "file": "/tmp/solis_etoday_kwh.txt",
        "keys": ["eToday"],
        "multiply": 1,
        "description": "Energy generated today (kWh)",
    },
    "etotal_kwh": {
        "file": "/tmp/solis_etotal_kwh.txt",
        "keys": ["allEnergyOriginal", "eTotal"],
        "multiply": 1,
        "description": "Total lifetime energy (kWh); eTotal is MWh, allEnergyOriginal is kWh",
    },
    "grid_import_w": {
        "file": "/tmp/solis_grid_import_w.txt",
        "keys": ["acGridPowerA"],
        "multiply": 1,
        "description": "Grid import power (W); API negative=import, shown as magnitude",
    },
    "backup_w": {
        "file": "/tmp/solis_backup_w.txt",
        "keys": ["backupPowerA"],
        "multiply": 1,
        "description": "Backup/essential circuits load (W)",
    },
    "smart_w": {
        "file": "/tmp/solis_smart_w.txt",
        "keys": [],
        "multiply": 1,
        "description": "Smart/non-essential load (W); computed as total load - backup load",
    },
}

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
CONFIG_DIR = os.path.expanduser("~/.config/solis2kde")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.yaml")

# ---------------------------------------------------------------------------
# SolisCloud API endpoints
# ---------------------------------------------------------------------------
BASE_URL = "https://www.soliscloud.com:13333"
INVERTER_LIST_URL = f"{BASE_URL}/v1/api/inverterList"
INVERTER_DETAIL_URL = f"{BASE_URL}/v1/api/inverterDetail"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log = logging.getLogger("solis2kde")


# ===================================================================
# Simple YAML parser (subset: scalar key-value pairs only)
# ===================================================================
def _parse_yaml(text):
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip().strip("\"'")
        result[key] = val
    return result


# ===================================================================
# Config loading
# ===================================================================
def load_config():
    if not os.path.isfile(CONFIG_PATH):
        log.error("Config file not found: %s", CONFIG_PATH)
        sys.exit(1)

    with open(CONFIG_PATH, "r") as fh:
        raw = fh.read()

    cfg = _parse_yaml(raw)

    required = ["api_key", "api_secret", "station_id"]
    for k in required:
        if k not in cfg:
            log.error("Missing config key '%s' in %s", k, CONFIG_PATH)
            sys.exit(1)

    try:
        cfg["poll_interval"] = int(cfg.get("poll_interval", 300))
    except ValueError:
        log.warning("Invalid poll_interval, using default 300")
        cfg["poll_interval"] = 300

    return cfg


# ===================================================================
# HTTP helpers (stdlib only)
# ===================================================================
_CT_HEADER = "application/json;charset=UTF-8"
_CT_SIGN = "application/json"


def _content_md5(body_str):
    return base64.b64encode(
        hashlib.md5(body_str.encode("utf-8")).digest()
    ).decode("ascii")


def _sign(api_id, api_secret, method, uri, body_str, date_str, ct_sign):
    md5_body = _content_md5(body_str)
    string_to_sign = f"{method}\n{md5_body}\n{ct_sign}\n{date_str}\n{uri}"
    sig = hmac.new(
        api_secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    encoded = base64.b64encode(sig).decode("ascii")
    return md5_body, f"API {api_id}:{encoded}"


def _request(method, url, body=None, api_key=None, api_secret=None, sign_uri=None):
    date_str = email.utils.formatdate(usegmt=True)
    headers = {
        "Content-Type": _CT_HEADER,
        "Date": date_str,
    }

    data = None
    body_str = ""
    if body is not None:
        body_str = json.dumps(body, separators=(",", ":"))
        data = body_str.encode("utf-8")

    if api_key is not None and api_secret is not None and sign_uri is not None:
        md5_body, auth_val = _sign(
            api_key, api_secret, method, sign_uri, body_str, date_str, _CT_SIGN
        )
        headers["Content-MD5"] = md5_body
        headers["Authorization"] = auth_val

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            ct = resp.headers.get("Content-Type", "")
            if "application/json" in ct:
                return resp.status, json.loads(raw)
            return resp.status, raw
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        return exc.code, body_text
    except urllib.error.URLError as exc:
        return 0, str(exc.reason)


# ===================================================================
# SolisCloud API
# ===================================================================
def fetch_inverter_list(api_key, api_secret, station_id):
    """Return the list of inverters for a station."""
    status, data = _request(
        "POST", INVERTER_LIST_URL,
        body={"id": station_id, "pageNo": 1, "pageSize": 10},
        api_key=api_key,
        api_secret=api_secret,
        sign_uri="/v1/api/inverterList",
    )
    if status != 200:
        raise RuntimeError(f"inverterList failed (HTTP {status}): {data}")
    records = (data.get("data") or {}).get("page", {}).get("records", [])
    return list(records)


def fetch_inverter_data(api_key, api_secret, inverter_id):
    """Return inverter detail dict with all real-time metrics."""
    status, data = _request(
        "POST", INVERTER_DETAIL_URL,
        body={"id": inverter_id},
        api_key=api_key,
        api_secret=api_secret,
        sign_uri="/v1/api/inverterDetail",
    )
    if status != 200:
        raise RuntimeError(f"inverterDetail failed (HTTP {status}): {data}")
    return data.get("data") or data


def resolve_inverter_id(api_key, api_secret, station_id, prefer_inverter_id=None):
    """Return the first inverter ID for the station, or the preferred one."""
    if prefer_inverter_id:
        return prefer_inverter_id
    inverters = fetch_inverter_list(api_key, api_secret, station_id)
    if not inverters:
        raise RuntimeError("No inverters found for this station")
    return inverters[0].get("id")


# ===================================================================
# Field extraction
# ===================================================================
def _deep_get(obj, key):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() == key.lower():
                return v
            res = _deep_get(v, key)
            if res is not None:
                return res
    elif isinstance(obj, list):
        for item in obj:
            res = _deep_get(item, key)
            if res is not None:
                return res
    return None


def extract_value(data, candidates, multiply=1):
    for key in candidates:
        val = _deep_get(data, key)
        if val is not None:
            try:
                return float(val) * multiply
            except (ValueError, TypeError):
                continue
    return None


# ===================================================================
# Atomic file write
# ===================================================================
def atomic_write(path, value):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(str(value))
    os.rename(tmp, path)


# ===================================================================
# Grid power handling
# ===================================================================
def resolve_grid(grid_val):
    if grid_val is None or grid_val == 0:
        return 0.0, "zero"
    if grid_val > 0:
        return grid_val, "export"
    return abs(grid_val), "import"


# ===================================================================
# Load fallback: solar + grid (signed)
# ===================================================================
def resolve_load(data, solar_val, grid_val):
    load_entry = FIELD_MAP["load_w"]
    load_val = extract_value(data, load_entry["keys"], load_entry.get("multiply", 1))
    if load_val is not None:
        return load_val
    if solar_val is not None and grid_val is not None:
        return solar_val + grid_val
    return None


# ===================================================================
# Main loop
# ===================================================================
def main_loop(cfg):
    interval = cfg["poll_interval"]
    consec_fails = 0
    inverter_id_cache = [None]

    while True:
        try:
            if inverter_id_cache[0] is None:
                log.info("Discovering inverter ID ...")
                inverter_id_cache[0] = resolve_inverter_id(
                    cfg["api_key"], cfg["api_secret"], cfg["station_id"]
                )
                log.info("Using inverter ID: %s", inverter_id_cache[0])

            log.info("Fetching inverter data ...")
            data = fetch_inverter_data(
                cfg["api_key"], cfg["api_secret"], inverter_id_cache[0]
            )
        except Exception as exc:
            consec_fails += 1
            level = logging.ERROR if consec_fails >= 3 else logging.WARNING
            log.log(level, "Fetch failed (%d consecutive): %s", consec_fails, exc)
            if consec_fails >= 5:
                inverter_id_cache[0] = None
            time.sleep(interval)
            continue

        consec_fails = 0

        solar_val = extract_value(
            data, FIELD_MAP["solar_w"]["keys"], FIELD_MAP["solar_w"].get("multiply", 1)
        )
        grid_val = extract_value(
            data, FIELD_MAP["grid_w"]["keys"], FIELD_MAP["grid_w"].get("multiply", 1)
        )
        grid_mag, grid_dir = resolve_grid(grid_val)
        battery_val = extract_value(
            data, FIELD_MAP["battery_pct"]["keys"], FIELD_MAP["battery_pct"].get("multiply", 1)
        )
        etoday_val = extract_value(
            data, FIELD_MAP["etoday_kwh"]["keys"], FIELD_MAP["etoday_kwh"].get("multiply", 1)
        )
        etotal_val = extract_value(
            data, FIELD_MAP["etotal_kwh"]["keys"], FIELD_MAP["etotal_kwh"].get("multiply", 1)
        )
        load_val = resolve_load(data, solar_val, grid_val)

        grid_import_val = extract_value(
            data, FIELD_MAP["grid_import_w"]["keys"], FIELD_MAP["grid_import_w"].get("multiply", 1)
        )
        if grid_import_val is not None and grid_import_val < 0:
            grid_import_val = abs(grid_import_val)
        elif grid_import_val is not None and grid_import_val > 0:
            grid_import_val = 0.0

        backup_val = extract_value(
            data, FIELD_MAP["backup_w"]["keys"], FIELD_MAP["backup_w"].get("multiply", 1)
        )
        smart_val = None
        if load_val is not None and backup_val is not None:
            smart_val = max(0.0, load_val - backup_val)

        vals = {
            "solar_w": solar_val if solar_val is not None else 0.0,
            "grid_w": grid_mag,
            "grid_import_w": grid_import_val if grid_import_val is not None else 0.0,
            "battery_pct": battery_val if battery_val is not None else 0.0,
            "etoday_kwh": etoday_val if etoday_val is not None else 0.0,
            "etotal_kwh": etotal_val if etotal_val is not None else 0.0,
            "load_w": load_val if load_val is not None else 0.0,
            "backup_w": backup_val if backup_val is not None else 0.0,
            "smart_w": smart_val if smart_val is not None else 0.0,
        }

        for name, val in vals.items():
            fpath = FIELD_MAP[name]["file"]
            try:
                atomic_write(fpath, val)
            except OSError as exc:
                log.warning("Cannot write %s: %s", fpath, exc)

        parts = []
        if solar_val is not None:
            parts.append(f"solar={solar_val:.0f}W")
        if grid_val is not None:
            parts.append(f"grid={grid_dir}={grid_mag:.0f}W")
        if grid_import_val is not None:
            parts.append(f"grid_import={grid_import_val:.0f}W")
        if backup_val is not None:
            parts.append(f"backup={backup_val:.0f}W")
        if smart_val is not None:
            parts.append(f"smart={smart_val:.0f}W")
        if load_val is not None:
            parts.append(f"load={load_val:.0f}W")
        if battery_val is not None:
            parts.append(f"battery={battery_val:.0f}%")
        if etoday_val is not None:
            parts.append(f"etoday={etoday_val:.1f}kWh")
        if etotal_val is not None:
            parts.append(f"etotal={etotal_val:.0f}kWh")
        log.info("Station data: %s", ", ".join(parts))

        time.sleep(interval)


# ===================================================================
# --discover mode
# ===================================================================
def discover():
    cfg = load_config()
    try:
        inv_id = resolve_inverter_id(
            cfg["api_key"], cfg["api_secret"], cfg["station_id"]
        )
        data = fetch_inverter_data(cfg["api_key"], cfg["api_secret"], inv_id)
    except Exception as exc:
        log.error("Discovery failed: %s", exc)
        sys.exit(1)

    print(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True))


# ===================================================================
# Entry point
# ===================================================================
def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stderr,
    )

    if "--discover" in sys.argv:
        discover()
        return

    cfg = load_config()
    try:
        main_loop(cfg)
    except KeyboardInterrupt:
        log.info("Shutting down.")


if __name__ == "__main__":
    main()
