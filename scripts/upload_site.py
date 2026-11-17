"""Laster site/*.json opp i Neon (tabell site_files), så de kan leveres bak innlogging."""
import glob, os, sys, psycopg
url = os.environ.get("NEON_DATABASE_URL")
if not url:
    print("NEON_DATABASE_URL mangler"); sys.exit(0)
with psycopg.connect(url) as con:
    con.execute("create table if not exists site_files (name text primary key, body text not null, updated_at timestamptz not null default now())")
    n = 0
    for p in sorted(glob.glob("site/*.json")):
        name = os.path.basename(p)
        body = open(p, encoding="utf-8").read()
        con.execute("insert into site_files (name, body, updated_at) values (%s,%s,now()) on conflict (name) do update set body=excluded.body, updated_at=now()", (name, body))
        n += 1
print("Lastet opp", n, "filer")
