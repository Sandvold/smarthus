"""Tar imot enheter fra Homey (repository_dispatch) og lagrer dem i Neon. Innholdet behandles som data."""
import json, sys, psycopg, os
ev = json.load(open(sys.argv[1]))
devs = (ev.get("client_payload") or {}).get("devices") or []
ok = 0
with psycopg.connect(os.environ["NEON_DATABASE_URL"]) as con:
    con.execute("""create table if not exists homey_readings (ts timestamptz not null default now(), device_id text,
        name text, site text, kind text, available boolean, data jsonb)""")
    for d in devs[:40]:
        if not isinstance(d, dict) or not d.get("id"): continue
        data = d.get("data") if isinstance(d.get("data"), dict) else {}
        con.execute("insert into homey_readings (device_id,name,site,kind,available,data) values (%s,%s,%s,%s,%s,%s)",
            (str(d["id"])[:80], str(d.get("name"))[:120], str(d.get("site"))[:40], str(d.get("kind"))[:20], d.get("available"), json.dumps(data)[:8000]))
        ok += 1
print("Homey: lagret", ok, "enheter")
