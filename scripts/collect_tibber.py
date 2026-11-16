"""Henter forbruk og strømpris fra Tibber, lagrer i Neon og skriver site/power.json."""
import json, os, sys
import requests, psycopg

TOKEN = os.environ.get("TIBBER_TOKEN")
if not TOKEN:
    print("TIBBER_TOKEN mangler, hopper over Tibber")
    sys.exit(0)

SITE = json.load(open("config/tibber.json")).get("site", "ukjent")
Q = """{ viewer { homes { id appNickname features { realTimeConsumptionEnabled }
  consumption(resolution: HOURLY, last: 72) { nodes { from to consumption cost unitPrice } }
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
    for h in homes:
        for n in ((h.get("consumption") or {}).get("nodes") or []):
            if n.get("consumption") is None:
                continue
            con.execute("""insert into power_hourly (home_id,site,ts_from,kwh,cost,unit_price)
                values (%s,%s,%s,%s,%s,%s) on conflict (home_id, ts_from) do update
                set kwh=excluded.kwh, cost=excluded.cost, unit_price=excluded.unit_price""",
                (h["id"], SITE, n["from"], n["consumption"], n.get("cost"), n.get("unitPrice")))
        pi = ((h.get("currentSubscription") or {}).get("priceInfo")) or {}
        for key in ("today", "tomorrow"):
            for p in pi.get(key) or []:
                con.execute("""insert into price_hourly (home_id,starts_at,total) values (%s,%s,%s)
                    on conflict (home_id, starts_at) do update set total=excluded.total""",
                    (h["id"], p["startsAt"], p["total"]))
    hid = homes[0]["id"] if homes else None
    hourly = con.execute("""select ts_from, kwh, cost from power_hourly where home_id=%s
        and ts_from > now() - interval '14 days' order by ts_from""", (hid,)).fetchall() if hid else []
    prices = con.execute("""select starts_at, total from price_hourly where home_id=%s
        and starts_at > now() - interval '2 days' order by starts_at""", (hid,)).fetchall() if hid else []

os.makedirs("site", exist_ok=True)
json.dump({"site": SITE,
           "hourly": [{"from": a.isoformat(), "kwh": float(b), "cost": None if c is None else float(c)} for a, b, c in hourly],
           "prices": [{"from": a.isoformat(), "total": float(b)} for a, b in prices]},
          open("site/power.json", "w"))
print(f"Tibber: {len(hourly)} timer, {len(prices)} priser")
