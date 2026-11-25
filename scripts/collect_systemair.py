"""Henter Systemair SAVE-ventilasjon via Homesolutions-skyen (uoffisielt GraphQL, OIDC passordinnlogging). Skriver site/systemair.json."""
import json, os, sys
import requests, psycopg

EMAIL, PW = os.environ.get("SYSTEMAIR_EMAIL"), os.environ.get("SYSTEMAIR_PASSWORD")
if not (EMAIL and PW):
    print("SYSTEMAIR_EMAIL/PASSWORD mangler, hopper over Systemair"); sys.exit(0)
TOKEN_URL = "https://sso.systemair.com/auth/realms/iot/protocol/openid-connect/token"
API = "https://homesolutions.systemair.com/gateway/api"
SITE = "Arendal"
OUT = {"error": None, "devices": []}

def done():
    os.makedirs("site", exist_ok=True); json.dump(OUT, open("site/systemair.json", "w"), ensure_ascii=False)

def gql(tok, query, variables=None):
    r = requests.post(API, json={"query": query, "variables": variables or {}}, headers={"x-access-token": tok}, timeout=60)
    j = r.json() if r.content else {}
    if r.status_code != 200 or j.get("errors"):
        raise RuntimeError(f"HTTP {r.status_code} " + str((j.get("errors") or [{}])[0].get("message", ""))[:160])
    return j["data"]

try:
    r = requests.post(TOKEN_URL, data={"grant_type": "password", "username": EMAIL, "password": PW, "client_id": "iot-application", "scope": "openid email profile"}, timeout=30)
    j = r.json() if r.content else {}
    tok = j.get("access_token")
    if not tok:
        OUT["error"] = "Innlogging avvist: " + str(j.get("error_description") or j.get("error") or r.status_code)[:120] + " (sett eget passord på kontoen hos Systemair?)"
        done(); print(OUT["error"]); sys.exit(0)
    devs = gql(tok, "{ GetAccountDevices { identifier name } }")["GetAccountDevices"] or []
    rows = []
    for d in devs:
        did = d["identifier"]
        try:
            v = gql(tok, "mutation ($input: GetDeviceViewInput!) { GetDeviceView(input: $input) { route dataItems } }", {"input": {"deviceId": did, "route": "/device/home"}})["GetDeviceView"]
            regs = {}
            for it in v.get("dataItems") or []:
                if isinstance(it, dict) and "register" in it and it.get("value") is not None:
                    regs[str(it["register"])] = it["value"]
            print(f"Systemair {d.get('name')}: {len(regs)} registre:", ",".join(sorted(regs, key=int))[:400])
            rows.append((did, d.get("name") or did, regs))
        except Exception as e:
            print("Systemair enhet feilet:", str(e)[:200]); OUT["error"] = str(e)[:200]
    with psycopg.connect(os.environ["NEON_DATABASE_URL"]) as con:
        con.execute("create table if not exists systemair_readings (ts timestamptz not null default now(), device_id text, name text, regs jsonb)")
        con.execute("create index if not exists systemair_ts on systemair_readings (ts)")
        for did, n, regs in rows:
            if regs: con.execute("insert into systemair_readings (device_id,name,regs) values (%s,%s,%s::jsonb)", (did, n, json.dumps(regs)))
        out = {}
        for did, n, ts, regs in con.execute("select device_id,name,ts,regs from systemair_readings where ts > now() - interval '30 days' order by ts").fetchall():
            o = out.setdefault(did, {"id": did, "name": n, "site": SITE, "rows": []})
            o["rows"].append({"t": ts.isoformat(), "r": regs})
        OUT["devices"] = list(out.values())
except Exception as e:
    OUT["error"] = f"{type(e).__name__}: {str(e)[:200]}"; print(OUT["error"])
done()
