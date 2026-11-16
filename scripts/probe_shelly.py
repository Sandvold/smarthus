"""Lister Shelly-enheter fra skyen (til oppsett). Skriver ut id, navn og status."""
import json, os, requests

server = os.environ["SHELLY_SERVER"].strip().rstrip("/")
if not server.startswith("http"):
    server = "https://" + server
key = os.environ["SHELLY_AUTH_KEY"].strip()

r = requests.post(server + "/device/all_status", data={"show_info": "true", "auth_key": key}, timeout=60)
print("HTTP", r.status_code)
j = r.json()
if not j.get("isok"):
    print("FEIL:", json.dumps(j)[:400])
    raise SystemExit(1)
devs = (j.get("data") or {}).get("devices_status") or {}
print("Antall enheter:", len(devs))
for did, st in devs.items():
    info = st.get("_dev_info") or {}
    keep = {k: v for k, v in st.items() if k not in ("_dev_info", "wifi_sta", "cloud", "mqtt", "sys", "ble", "ws", "eth", "time", "unixtime", "serial", "has_update", "mac", "cfg_rev", "actions_stats", "ram_total", "ram_free", "fs_size", "fs_free", "uptime", "ext_sensors", "ext_temperature", "ext_humidity")}
    print("==", did, "|", info.get("code"), "|", info.get("gen"), "| online:", info.get("online"))
    print(json.dumps(keep, ensure_ascii=False)[:900])
