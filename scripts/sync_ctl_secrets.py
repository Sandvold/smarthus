"""Kopierer nøklene (Aqara, Shelly, Adax, Gardena, Zaptec, Homey) fra GitHub-secrets til Neon (ctl_secrets), så styringsfunksjonen kan bruke dem uten at de limes inn noe sted."""
import os, sys, psycopg
url = os.environ.get("NEON_DATABASE_URL")
if not url or not os.environ.get("AQARA_APP_KEY"):
    print("mangler secrets, hopper over"); sys.exit(0)
with psycopg.connect(url) as con:
    con.execute("create table if not exists ctl_secrets (name text primary key, value text not null, updated_at timestamptz default now())")
    for k in ("AQARA_APP_ID", "AQARA_KEY_ID", "AQARA_APP_KEY", "AQARA_REGION", "HOMEY_LOCK_URL",
              "SHELLY_SERVER", "SHELLY_AUTH_KEY", "ADAX_ACCOUNT_ID", "ADAX_PASSWORD",
              "GARDENA_CLIENT_ID", "GARDENA_CLIENT_SECRET", "GARDENA_CLIENT_ID_2", "GARDENA_CLIENT_SECRET_2",
              "ZAPTEC_USERNAME", "ZAPTEC_PASSWORD", "TUYA_ACCESS_ID", "TUYA_ACCESS_SECRET", "TUYA_HOST", "OJ_USERNAME", "OJ_PASSWORD", "OJ_API_KEY", "OJ_CUSTOMER_ID", "OJ_HOST"):
        v = (os.environ.get(k) or "").strip()
        if v:
            con.execute("insert into ctl_secrets (name,value,updated_at) values (%s,%s,now()) on conflict (name) do update set value=excluded.value, updated_at=now()", (k, v))
print("Styringsoppsett synkronisert")
