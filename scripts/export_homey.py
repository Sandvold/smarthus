"""Skriver site/homey.json fra Neon (siste 14 dager av Homey-data)."""
import json, os, sys, psycopg
sys.path.insert(0, os.path.dirname(__file__))
import incr
with psycopg.connect(os.environ["NEON_DATABASE_URL"]) as con:
    ok = con.execute("select to_regclass('homey_readings')").fetchone()[0]
    if not ok:
        print("Ingen Homey-data ennå"); sys.exit(0)
    prev, since = incr.prev_entities("site/homey.json", "devices", lambda e: e.get("id"))
    rows = con.execute("select device_id,name,site,kind,ts,available,data from homey_readings where ts > %s order by ts", (since or incr.window(14),)).fetchall()
out = {}
for did, name, site, kind, ts, av, data in rows:
    d = out.setdefault(did, {"id": did, "name": name, "site": site, "kind": kind, "rows": []})
    d["name"], d["site"], d["kind"] = name, site, kind
    d["rows"].append({"t": ts.isoformat(), "av": av, "d": data})
out = incr.merge(out, prev, 14)
json.dump({"devices": list(out.values())}, open("site/homey.json", "w"), ensure_ascii=False)
print("Homey: enheter", len(out), "rader", len(rows))
