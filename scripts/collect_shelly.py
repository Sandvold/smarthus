"""Henter effekt og energi per kanal fra Shelly Cloud, lagrer i Neon og skriver site/shelly.json."""
import json, os, sys, requests, psycopg

KEY = os.environ.get("SHELLY_AUTH_KEY", "").strip()
SERVER = os.environ.get("SHELLY_SERVER", "").strip().rstrip("/")
if not KEY or not SERVER:
    print("Shelly-secrets mangler, hopper over"); sys.exit(0)
if not SERVER.startswith("http"):
    SERVER = "https://" + SERVER
CFG = json.load(open("config/shelly.json"))["devices"]

r = requests.post(SERVER + "/device/all_status", data={"auth_key": KEY}, timeout=60).json()
if not r.get("isok"):
    raise RuntimeError("Shelly-feil: " + json.dumps(r)[:300])
devs = r["data"]["devices_status"]

with psycopg.connect(os.environ["NEON_DATABASE_URL"]) as con:
    con.execute("""create table if not exists shelly_readings (ts timestamptz not null default now(),
        device_id text, channel int, name text, site text, apower numeric, wh_total numeric, output boolean, voltage numeric)""")
    for did, st in devs.items():
        c = CFG.get(did)
        if not c: continue
        for k, v in st.items():
            if not k.startswith("switch:"): continue
            ch = int(k.split(":")[1])
            con.execute("insert into shelly_readings (device_id,channel,name,site,apower,wh_total,output,voltage) values (%s,%s,%s,%s,%s,%s,%s,%s)",
                (did, ch, c["channels"].get(str(ch), f"Kanal {ch+1}"), c["site"], v.get("apower"), (v.get("aenergy") or {}).get("total"), v.get("output"), v.get("voltage")))
    rows = con.execute("""select device_id, channel, name, site, ts, apower, wh_total, output from shelly_readings
        where ts > now() - interval '14 days' order by ts""").fetchall()
out = {}
for did, ch, name, site, ts, ap, wh, o in rows:
    d = out.setdefault(f"{did}:{ch}", {"name": name, "site": site, "rows": []})
    d["rows"].append({"t": ts.isoformat(), "w": float(ap) if ap is not None else None, "wh": float(wh) if wh is not None else None, "on": o})
json.dump({"channels": list(out.values())}, open("site/shelly.json", "w"), ensure_ascii=False)
print("Shelly: kanaler", len(out), "rader", len(rows))
