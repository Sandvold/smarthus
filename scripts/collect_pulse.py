"""Henter sanntidseffekt fra Tibber Pulse (kort tilkobling til websocket), lagrer i Neon og skriver site/pulse.json."""
import json, os, sys, threading, time
import requests, psycopg

TOKEN = os.environ.get("TIBBER_TOKEN")
if not TOKEN:
    print("TIBBER_TOKEN mangler, hopper over Pulse"); sys.exit(0)
CFG = json.load(open("config/tibber.json")).get("homes", {})
SECONDS = 14
SUB = """subscription($id: ID!) { liveMeasurement(homeId: $id) { timestamp power powerProduction accumulatedConsumption
  minPower averagePower maxPower voltagePhase1 voltagePhase2 voltagePhase3 currentL1 currentL2 currentL3 } }"""

def home_cfg(hid, i):
    for pre, c in CFG.items():
        if hid.startswith(pre):
            return c
    return {"name": f"Hjem {i}", "site": f"hjem{i}"}

r = requests.post("https://api.tibber.com/v1-beta/gql", headers={"Authorization": "Bearer " + TOKEN}, timeout=30,
    json={"query": "{ viewer { websocketSubscriptionUrl homes { id features { realTimeConsumptionEnabled } } } }"}).json()
if r.get("errors"):
    raise RuntimeError("Tibber-feil: " + json.dumps(r["errors"])[:300])
v = r["data"]["viewer"]; url = v["websocketSubscriptionUrl"]; homes = v["homes"]

def listen(hid, sink):
    from websockets.sync.client import connect
    try:
        with connect(url, subprotocols=["graphql-transport-ws"], open_timeout=15,
                     additional_headers={"User-Agent": "smarthus/1.0"}) as ws:
            ws.send(json.dumps({"type": "connection_init", "payload": {"token": TOKEN}}))
            ack = json.loads(ws.recv(timeout=15))
            if ack.get("type") != "connection_ack":
                sink["error"] = "ingen ack: " + str(ack)[:120]; return
            ws.send(json.dumps({"id": "1", "type": "subscribe", "payload": {"query": SUB, "variables": {"id": hid}}}))
            end = time.time() + SECONDS
            while time.time() < end:
                try:
                    m = json.loads(ws.recv(timeout=max(0.5, end - time.time())))
                except TimeoutError:
                    break
                if m.get("type") == "next":
                    d = ((m.get("payload") or {}).get("data") or {}).get("liveMeasurement")
                    if d: sink.setdefault("msgs", []).append(d)
                elif m.get("type") == "error":
                    sink["error"] = json.dumps(m.get("payload"))[:200]; return
    except Exception as e:
        sink["error"] = type(e).__name__ + ": " + str(e)[:150]

res, th = {}, []
for h in homes:
    en = bool((h.get("features") or {}).get("realTimeConsumptionEnabled"))
    res[h["id"]] = {"enabled": en}
    if en:
        t = threading.Thread(target=listen, args=(h["id"], res[h["id"]])); t.start(); th.append(t)
for t in th: t.join(SECONDS + 25)

with psycopg.connect(os.environ["NEON_DATABASE_URL"]) as con:
    con.execute("""create table if not exists pulse_readings (home_id text not null, site text, ts timestamptz not null default now(),
        w numeric, min_w numeric, max_w numeric, avg_w numeric, acc_kwh numeric, v1 numeric, i1 numeric, i2 numeric, i3 numeric, n int)""")
    con.execute("create index if not exists pulse_ts on pulse_readings (ts)")
    out = []
    for i, h in enumerate(homes):
        c = home_cfg(h["id"], i); s = res[h["id"]]; ms = s.get("msgs") or []
        if ms:
            p = [m["power"] for m in ms if m.get("power") is not None]
            if p:
                l = ms[-1]
                con.execute("insert into pulse_readings (home_id,site,w,min_w,max_w,avg_w,acc_kwh,v1,i1,i2,i3,n) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (h["id"], c["site"], sum(p)/len(p), min(p), max(p), l.get("averagePower"), l.get("accumulatedConsumption"),
                     l.get("voltagePhase1"), l.get("currentL1"), l.get("currentL2"), l.get("currentL3"), len(p)))
        rows = con.execute("select ts,w,min_w,max_w,i1,i2,i3,v1 from pulse_readings where home_id=%s and ts > now() - interval '30 days' order by ts", (h["id"],)).fetchall()
        f = lambda x: None if x is None else float(x)
        out.append({"name": c["name"], "site": c["site"], "enabled": s["enabled"], "error": s.get("error"), "msgs": len(ms),
                    "rows": [{"t": a.isoformat(), "w": f(b), "min": f(mn), "max": f(mx), "i": [f(x) for x in (i1, i2, i3)], "v": f(v1)} for a, b, mn, mx, i1, i2, i3, v1 in rows]})
        print(f"Pulse {c['name']}: aktiv={s['enabled']} meldinger={len(ms)} feil={s.get('error')}")
os.makedirs("site", exist_ok=True)
json.dump({"homes": out}, open("site/pulse.json", "w"))
