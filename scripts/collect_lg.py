"""Henter LG ThinQ-apparater (ovn m.m.) via ThinQ Connect API med personlig token. Skriver site/lg.json."""
import json, os, sys, uuid, secrets
import requests, psycopg

PAT = os.environ.get("LG_THINQ_PAT")
if not PAT:
    print("LG_THINQ_PAT mangler, hopper over LG"); sys.exit(0)
COUNTRY = os.environ.get("LG_COUNTRY") or "NO"
SITE_BY_TYPE = {"DEVICE_WASHER": "Arendal", "DEVICE_WASHTOWER": "Arendal", "DEVICE_DRYER": "Arendal"}
site_of = lambda t: SITE_BY_TYPE.get(t, "Oslo")
API_KEY = "v6GFvkweNo7DK7yD3ylIZ9w52aKBU0eJ7wLXkSR3"
OUT = {"error": None, "devices": [], "tried": []}
CLIENT = "smarthus-" + uuid.uuid5(uuid.NAMESPACE_DNS, "sandvold-smarthus").hex[:16]

def hdr():
    return {"Authorization": "Bearer " + PAT, "x-country": COUNTRY, "x-message-id": secrets.token_urlsafe(16)[:22],
            "x-client-id": CLIENT, "x-api-key": API_KEY, "x-service-phase": "OP", "Content-Type": "application/json"}

def done():
    os.makedirs("site", exist_ok=True); json.dump(OUT, open("site/lg.json", "w"))

def flat(d, pre="", out=None):
    out = {} if out is None else out
    if isinstance(d, dict):
        for k, v in d.items(): flat(v, pre + ("." if pre else "") + str(k), out)
    elif isinstance(d, list):
        for i, v in enumerate(d[:4]): flat(v, f"{pre}[{i}]", out)
    elif d is not None:
        out[pre] = d
    return out

base = None; devs = []
for reg in ("eic", "aic", "kic"):
    try:
        r = requests.get(f"https://api-{reg}.lgthinq.com/devices", headers=hdr(), timeout=30)
        j = r.json() if r.content else {}
    except Exception as e:
        OUT["tried"].append(f"{reg}: {type(e).__name__}"); continue
    OUT["tried"].append(f"{reg}: HTTP {r.status_code} " + str((j.get("error") or {}).get("message") or "")[:100] if isinstance(j, dict) else f"{reg}: HTTP {r.status_code}")
    if r.status_code == 200:
        base = f"https://api-{reg}.lgthinq.com"; devs = j.get("response") or []; break
if not base:
    OUT["error"] = "Fikk ikke tilgang til LG ThinQ (token, land eller region avvist)."; done(); print(OUT["error"]); sys.exit(0)
rows = []
for d in devs:
    did = d.get("deviceId"); info = d.get("deviceInfo") or {}
    try:
        st = requests.get(f"{base}/devices/{did}/state", headers=hdr(), timeout=30).json().get("response") or {}
    except Exception:
        st = {}
    rows.append((did, info.get("alias") or info.get("deviceType") or did, info.get("deviceType"), flat(st)))
with psycopg.connect(os.environ["NEON_DATABASE_URL"]) as con:
    con.execute("""create table if not exists lg_readings (ts timestamptz not null default now(), device_id text, name text, type text, state jsonb)""")
    con.execute("create index if not exists lg_ts on lg_readings (ts)")
    for did, n, t, s in rows:
        con.execute("insert into lg_readings (device_id,name,type,state) values (%s,%s,%s,%s::jsonb)", (did, n, t, json.dumps(s)))
    out = {}
    for did, n, t, ts, s in con.execute("select device_id,name,type,ts,state from lg_readings where ts > now() - interval '30 days' order by ts").fetchall():
        o = out.setdefault(did, {"name": n, "type": t, "site": site_of(t), "rows": []})
        o["rows"].append({"t": ts.isoformat(), "s": s})
OUT["devices"] = list(out.values()); OUT["tried"] = OUT["tried"][-1:]
done(); print("LG: enheter", len(rows))
