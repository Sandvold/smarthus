"""Henter forbruk og strømpris fra Tibber, lagrer i Neon og skriver site/power.json."""
import json, os, sys
import requests, psycopg

TOKEN = os.environ.get("TIBBER_TOKEN")
if not TOKEN:
    print("TIBBER_TOKEN mangler, hopper over Tibber")
    sys.exit(0)

CFG = json.load(open("config/tibber.json")).get("homes", {})

def home_cfg(hid, i):
    for pre, c in CFG.items():
        if hid.startswith(pre):
            return c
    return {"name": f"Hjem {i}", "site": f"hjem{i}"}
Q = """{ viewer { homes { id appNickname features { realTimeConsumptionEnabled }
  consumption(resolution: HOURLY, last: 2000) { nodes { from to consumption cost unitPrice } }
  currentSubscription { priceInfo { current { total startsAt } today { total startsAt } tomorrow { total startsAt } } } } } }"""

r = requests.post("https://api.tibber.com/v1-beta/gql", json={"query": Q},
                  headers={"Authorization": "Bearer " + TOKEN}, timeout=30).json()
if r.get("errors"):
    raise RuntimeError("Tibber-feil: " + json.dumps(r["errors"])[:500])
homes = r["data"]["viewer"]["homes"]
for i, h in enumerate(homes):
    n = len(((h.get("consumption") or {}).get("nodes")) or [])
    print(f"Hjem {i}: id={h['id'][:8]}… navn={h.get('appNickname')!r} pulse={((h.get('features') or {}).get('realTimeConsumptionEnabled'))} timer={n}")

with psycopg.connect(os.environ["NEON_DATABASE_URL"]) as con:
    con.execute("""create table if not exists power_hourly (home_id text not null, site text,
        ts_from timestamptz not null, kwh numeric, cost numeric, unit_price numeric,
        primary key (home_id, ts_from))""")
    con.execute("""create table if not exists price_hourly (home_id text not null,
        starts_at timestamptz not null, total numeric, primary key (home_id, starts_at))""")
    out = []
    for i, h in enumerate(homes):
        c = home_cfg(h["id"], i)
        for n in ((h.get("consumption") or {}).get("nodes") or []):
            if n.get("consumption") is None:
                continue
            con.execute("""insert into power_hourly (home_id,site,ts_from,kwh,cost,unit_price)
                values (%s,%s,%s,%s,%s,%s) on conflict (home_id, ts_from) do update
                set kwh=excluded.kwh, cost=excluded.cost, unit_price=excluded.unit_price""",
                (h["id"], c["site"], n["from"], n["consumption"], n.get("cost"), n.get("unitPrice")))
        pi = ((h.get("currentSubscription") or {}).get("priceInfo")) or {}
        for key in ("today", "tomorrow"):
            for p in pi.get(key) or []:
                con.execute("""insert into price_hourly (home_id,starts_at,total) values (%s,%s,%s)
                    on conflict (home_id, starts_at) do update set total=excluded.total""",
                    (h["id"], p["startsAt"], p["total"]))
        hourly = con.execute("""select ts_from, kwh, cost from power_hourly where home_id=%s
            and ts_from > now() - interval '120 days' order by ts_from""", (h["id"],)).fetchall()
        prices = con.execute("""select starts_at, total from price_hourly where home_id=%s
            and starts_at > now() - interval '2 days' order by starts_at""", (h["id"],)).fetchall()
        out.append({"name": c["name"], "site": c["site"],
                    "hourly": [{"from": a.isoformat(), "kwh": float(b), "cost": None if cc is None else float(cc)} for a, b, cc in hourly],
                    "prices": [{"from": a.isoformat(), "total": float(b)} for a, b in prices]})

os.makedirs("site", exist_ok=True)
json.dump({"homes": out}, open("site/power.json", "w"))
print("Tibber:", ", ".join(f"{o['name']}: {len(o['hourly'])} timer, {len(o['prices'])} priser" for o in out))
