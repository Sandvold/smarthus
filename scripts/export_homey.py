"""Skriver site/homey.json fra Neon (siste 14 dager av Homey-data)."""
import json, os, sys, psycopg
with psycopg.connect(os.environ["NEON_DATABASE_URL"]) as con:
    ok = con.execute("select to_regclass('homey_readings')").fetchone()[0]
    if not ok:
        print("Ingen Homey-data ennå"); sys.exit(0)
    rows = con.execute("select device_id,name,site,kind,ts,available,data from homey_readings where ts > now() - interval '14 days' order by ts").fetchall()
out = {}
for did, name, site, kind, ts, av, data in rows:
    d = out.setdefault(did, {"name": name, "site": site, "kind": kind, "rows": []})
    d["name"], d["site"], d["kind"] = name, site, kind
    d["rows"].append({"t": ts.isoformat(), "av": av, "d": data})
json.dump({"devices": list(out.values())}, open("site/homey.json", "w"), ensure_ascii=False)
print("Homey: enheter", len(out), "rader", len(rows))
