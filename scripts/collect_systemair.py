"""Henter Systemair SAVE-ventilasjon via Homesolutions-skyen (uoffisielt GraphQL, OIDC passordinnlogging). Skriver site/systemair.json."""
import json, os, re, sys
import requests, psycopg
sys.path.insert(0, os.path.dirname(__file__))
from systemair_auth import login

EMAIL, PW = (os.environ.get("SYSTEMAIR_EMAIL") or "").strip(), (os.environ.get("SYSTEMAIR_PASSWORD") or "").strip()
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
    tok, why = login(EMAIL, PW)
    if not tok:
        OUT["error"] = "Innlogging avvist: " + why + " (passord, eller kontoen bruker Google/Apple?)"
        done(); print(OUT["error"]); sys.exit(0)
    devs = gql(tok, "{ GetAccountDevices { identifier name deviceType { entry module scope type } } }")["GetAccountDevices"] or []
    VIEW = "query GetView($input: GetViewInputType!) { GetView(input: $input) { id title route children { type properties feature } error } }"
    def walk(o, regs, ids):
        if isinstance(o, dict):
            di = o.get("dataItem") if isinstance(o.get("dataItem"), dict) else None
            if di and isinstance(di.get("extension"), dict) and "modbusRegister" in di["extension"] and di.get("value") is not None:
                regs[str(di["extension"]["modbusRegister"])] = di["value"]
            for v in o.values(): walk(v, regs, ids)
        elif isinstance(o, list):
            for v in o: walk(v, regs, ids)
    # --- kø: kommandoer fra siden (ventilasjon) utføres her, før avlesningen ---
    ITEM = {"mode": 29, "airflow": 31, "temp": 32}
    WRITE = "mutation ($input: WriteDataItemsInput!) { WriteDataItems(input: $input) }"
    with psycopg.connect(os.environ["NEON_DATABASE_URL"]) as qc:
        qc.execute("create table if not exists systemair_queue (id bigserial primary key, created_at timestamptz not null default now(), field text not null, value int not null, status text not null default 'ny', result text, done_at timestamptz)")
        qc.execute("update systemair_queue set status = 'utløpt', done_at = now() where status = 'ny' and created_at < now() - interval '3 hours'")
        pend = qc.execute("select id, field, value from systemair_queue where status = 'ny' order by id").fetchall()
        for qid, field, value in pend:
            qc.execute("update systemair_queue set status = 'kjører' where id = %s", (qid,))
            qc.commit()
            res, st = "", "feil"
            try:
                if field not in ITEM or not devs: raise RuntimeError("ukjent felt eller ingen enhet")
                dev = devs[0]; typ = (dev.get("deviceType") or {}).get("type") or "TELTONIKA"
                r = requests.post("https://homesolutions.systemair.com/gateway/remote-api/", json={"operationName": None, "query": WRITE, "variables": {"input": {"dataPoints": [{"id": ITEM[field], "value": value}]}}}, headers={"x-access-token": tok, "device-id": dev["identifier"], "device-type": str(typ)}, timeout=60)
                j = r.json() if r.content else {}
                if r.status_code == 200 and not j.get("errors") and (j.get("data") or {}).get("WriteDataItems") is not False:
                    st, res = "ok", "utført " + str((j.get("data") or {}).get("WriteDataItems"))[:60]
                else:
                    res = "HTTP %s %s" % (r.status_code, str((j.get("errors") or [{}])[0].get("message") or j)[:200])
            except Exception as e:
                res = (type(e).__name__ + ": " + str(e))[:200]
            print("Systemair skriving", field, value, "->", st, res)
            qc.execute("update systemair_queue set status = %s, result = %s, done_at = now() where id = %s", (st, res, qid))
            qc.commit()
        if pend: import time; time.sleep(8)
    rows = []
    for d in devs:
        did = d["identifier"]; dt = d.get("deviceType") or {}
        print("Systemair enhet:", d.get("name"), "| type:", {k: dt.get(k) for k in ("entry", "module", "scope", "type")})
        regs = {}
        for typ in [t for t in (dt.get("module"), dt.get("type"), dt.get("entry"), "TELTONIKA") if t]:
            for route in ("/home", "/unit_information", "/week_schedule"):
                try:
                    r = requests.post("https://homesolutions.systemair.com/gateway/remote-api/", json={"operationName": "GetView", "query": VIEW, "variables": {"input": {"route": route, "viewId": ""}}}, headers={"x-access-token": tok, "device-id": did, "device-type": str(typ)}, timeout=60)
                    j = r.json() if r.content else {}
                    v = (j.get("data") or {}).get("GetView")
                    if v and not v.get("error"): walk(v.get("children"), regs, None)
                    else: print("  ", typ, route, "HTTP", r.status_code, str((j.get("errors") or [{}])[0].get("message") or (v or {}).get("error"))[:120])
                except Exception as e:
                    print("  ", typ, route, "feil", str(e)[:100])
            if regs: break
        print(f"Systemair {d.get('name')}: {len(regs)} registre:", ",".join(sorted(regs, key=int))[:500])
        rows.append((did, d.get("name") or did, regs))
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
