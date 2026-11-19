"""Strømforbruk per drivhus per døgn (kWh, dato i Europe/Oslo). Skriver site/drivhusstrom.json.
Kilder: Shelly (wh_total, Wh) i oppvarmet drivhus Arendal og Aqara-stikk (energy, kWh) i drivhuset i Oslo.
Døgnverdi = sum av positive endringer i telleren (tellerreset teller som 0)."""
import json, os, sys
import psycopg

DAYS = 60
SH = {"98a3167b1668": "Oppvarmet drivhus Arendal"}  # Shelly-enhet -> drivhus
AQ = {"lumi.54ef441000760552": "Lys drivhus", "lumi.54ef441000a0c5c6": "Ventilasjon drivhus",
      "lumi.54ef441000efb484": "Varme drivhus"}  # Aqara-stikk (alle i Drivhus Oslo)
con = psycopg.connect(os.environ["NEON_DATABASE_URL"], autocommit=True)

def daily(sql, params=()):
    out = {}
    for k, d, kwh in con.execute(sql, params).fetchall():
        out.setdefault(k, {})[d.isoformat()] = round(float(kwh), 3)
    return out

q_sh = """select device_id||'/'||channel k, (ts at time zone 'Europe/Oslo')::date d,
  sum(greatest(wh_total - prev, 0)) / 1000.0 from (
    select device_id, channel, ts, wh_total, lag(wh_total) over (partition by device_id, channel order by ts) prev
    from shelly_readings where wh_total is not null and ts > now() - make_interval(days => %s + 1)) x
  where prev is not null and ts > now() - make_interval(days => %s) group by 1, 2"""
q_aq = """select did k, (ts at time zone 'Europe/Oslo')::date d, sum(greatest(energy - prev, 0)) from (
    select did, ts, energy, lag(energy) over (partition by did order by ts) prev
    from aqara_readings where energy is not null and ts > now() - make_interval(days => %s + 1)) x
  where prev is not null and ts > now() - make_interval(days => %s) group by 1, 2"""
sh = daily(q_sh, (DAYS, DAYS))
aq = daily(q_aq, (DAYS, DAYS))

names = {r[0] + "/" + str(r[1]): r[2] for r in con.execute(
    "select distinct on (device_id, channel) device_id, channel, name from shelly_readings order by device_id, channel, ts desc").fetchall()}
sites = {}
for k, days in sh.items():
    dev = k.split("/")[0]
    if dev not in SH: continue
    sites.setdefault(SH[dev], []).append({"name": names.get(k, k), "days": days})
for did, days in aq.items():
    if did not in AQ: continue
    sites.setdefault("Drivhus Oslo", []).append({"name": AQ[did], "days": days})
# Shelly-kanaler uten forbruk i perioden (Uttak 3/4) er støy
for s in sites: sites[s] = [d for d in sites[s] if sum(d["days"].values()) > 0]
res = {"days": DAYS, "sites": [{"name": n, "devices": sorted(d, key=lambda x: -sum(x["days"].values()))} for n, d in sites.items()]}
os.makedirs("site", exist_ok=True); json.dump(res, open("site/drivhusstrom.json", "w"))
for s in res["sites"]:
    tot = {}
    for d in s["devices"]:
        for k, v in d["days"].items(): tot[k] = tot.get(k, 0) + v
    print(s["name"], {k: round(v, 2) for k, v in sorted(tot.items())})
