"""Kopierer Aqara-nøklene fra GitHub-secrets til Neon (ctl_secrets), så styringsfunksjonen kan bruke dem uten at de limes inn noe sted."""
import os, sys, psycopg
url = os.environ.get("NEON_DATABASE_URL")
if not url or not os.environ.get("AQARA_APP_KEY"):
    print("mangler secrets, hopper over"); sys.exit(0)
with psycopg.connect(url) as con:
    con.execute("create table if not exists ctl_secrets (name text primary key, value text not null, updated_at timestamptz default now())")
    for k in ("AQARA_APP_ID", "AQARA_KEY_ID", "AQARA_APP_KEY", "AQARA_REGION", "HOMEY_LOCK_URL"):
        v = (os.environ.get(k) or "").strip()
        if v:
            con.execute("insert into ctl_secrets (name,value,updated_at) values (%s,%s,now()) on conflict (name) do update set value=excluded.value, updated_at=now()", (k, v))
print("Styringsoppsett synkronisert")
