"""Henter romtemperatur og innstilt temperatur fra Adax, lagrer i Neon og skriver site/adax.json."""
import json, os, sys, requests, psycopg
ID, PW = os.environ.get("ADAX_ACCOUNT_ID", "").strip(), os.environ.get("ADAX_PASSWORD", "").strip()
if not ID or not PW:
    print("Adax-secrets mangler, hopper over"); sys.exit(0)
API = "https://api-1.adax.no/client-api"
_CFG = json.load(open("config/adax.json"))
NAMES = _CFG.get("homes", {})
IGNORE = set(_CFG.get("ignore_rooms", []))
t = requests.post(API + "/auth/token", data={"grant_type": "password", "username": ID, "password": PW}, timeout=30)
t.raise_for_status()
j = requests.get(API + "/rest/v1/content/", headers={"Authorization": "Bearer " + t.json()["access_token"]}, timeout=30).json()
homes = {h["id"]: h["name"] for h in j.get("homes", [])}
with psycopg.connect(os.environ["NEON_DATABASE_URL"]) as con:
    con.execute("""create table if not exists adax_readings (ts timestamptz not null default now(),
        room_id bigint, home text, room text, temp numeric, target numeric, heating boolean)""")
    for r in j.get("rooms", []):
        con.execute("insert into adax_readings (room_id,home,room,temp,target,heating) values (%s,%s,%s,%s,%s,%s)",
            (r["id"], homes.get(r.get("homeId")), r["name"],
             r["temperature"] / 100 if r.get("temperature") is not None else None,
             r["targetTemperature"] / 100 if r.get("targetTemperature") is not None else None,
             r.get("heatingEnabled")))
    rows = con.execute("select room_id, home, room, ts, temp, target, heating from adax_readings where ts > now() - interval '14 days' order by ts").fetchall()
out = {}
for rid, home, room, ts, temp, target, heating in rows:
    if rid in IGNORE: continue
    d = out.setdefault(rid, {"id": str(rid), "home": NAMES.get(home, home), "name": room, "rows": []})
    d["rows"].append({"t": ts.isoformat(), "temp": None if temp is None else float(temp), "target": None if target is None else float(target), "on": heating})
import time
hdr = {"Authorization": "Bearer " + t.json()["access_token"]}
now = int(time.time() * 1000)
for rid, d in out.items():
    try:
        q = requests.get(f"{API}/rest/v1/energy_log/{rid}?fromTime={now - 15 * 86400000}&toTime={now}", headers=hdr, timeout=30)
        d["energy"] = [[p["fromTime"], p["toTime"], round(p["energyWh"] / 1000, 3)] for p in (q.json().get("points") or []) if p.get("energyWh") is not None] if q.status_code == 200 else []
    except Exception as e:
        d["energy"] = []; print("Adax energi feilet", rid, type(e).__name__)
json.dump({"rooms": list(out.values())}, open("site/adax.json", "w"), ensure_ascii=False)
print("Adax: rom", len(out), "rader", len(rows))
