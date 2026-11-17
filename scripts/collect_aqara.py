"""Henter Aqara-enheter (stikk, temp/fukt) via Aqara Cloud. Skriver site/aqara.json."""
import hashlib, json, os, sys, time, uuid
import requests, psycopg

if not os.environ.get("AQARA_APP_ID"):
    print("Aqara-secrets mangler"); sys.exit(0)
APP_ID, KEY_ID, APP_KEY = (os.environ[k].strip() for k in ("AQARA_APP_ID", "AQARA_KEY_ID", "AQARA_APP_KEY"))
URL = f"https://{os.environ.get('AQARA_REGION', 'open-sg').strip()}.aqara.com/v3.0/open/api"
OUT = {"error": None, "devices": []}

def call(intent, data, token=None):
    h = {"Appid": APP_ID, "Keyid": KEY_ID, "Nonce": uuid.uuid4().hex, "Time": str(int(time.time() * 1000))}
    if token: h["Accesstoken"] = token
    s = "&".join(f"{k}={h[k]}" for k in sorted(h, key=str.lower)) + APP_KEY
    h["Sign"] = hashlib.md5(s.lower().encode()).hexdigest(); h["Content-Type"] = "application/json"
    return requests.post(URL, headers=h, json={"intent": intent, "data": data}, timeout=30).json()

def done(msg=None):
    OUT["error"] = msg; os.makedirs("site", exist_ok=True); json.dump(OUT, open("site/aqara.json", "w")); print("Aqara:", msg or len(OUT["devices"]), "enheter")

con = psycopg.connect(os.environ["NEON_DATABASE_URL"], autocommit=True)
con.execute("""create table if not exists aqara_readings (ts timestamptz not null default now(), did text, name text, model text, site text,
  temp numeric, hum numeric, power numeric, energy numeric, on_off boolean)""")
con.execute("create index if not exists aqara_ts on aqara_readings (ts)")
row = con.execute("select access, refresh, updated < now() - interval '3 days' from aqara_auth where id=1").fetchone()
if not row: done("Aqara er ikke godkjent ennå."); sys.exit(0)
access, refresh, old = row

def refresh_tok():
    global access
    r = call("config.auth.refreshToken", {"refreshToken": refresh}); res = r.get("result") or {}
    if not res.get("accessToken"): return r
    access = res["accessToken"]
    con.execute("update aqara_auth set access=%s, refresh=%s, updated=now() where id=1", (access, res.get("refreshToken") or refresh)); return None

if old:
    e = refresh_tok()
    if e: done(f"Fornyelse av Aqara-tilgang feilet (kode {e.get('code')}). Godkjenn på nytt."); sys.exit(0)
r = call("query.device.info", {"pageNum": 1, "pageSize": 50}, access)
if r.get("code") in (108, 107):
    e = refresh_tok()
    if e: done(f"Aqara-tilgangen er utløpt (kode {e.get('code')}). Godkjenn på nytt."); sys.exit(0)
    r = call("query.device.info", {"pageNum": 1, "pageSize": 50}, access)
if r.get("code") != 0: done(f"Aqara svarte kode {r.get('code')}: {r.get('messageDetail') or r.get('message')}"); sys.exit(0)
RES = ["0.1.85", "0.2.85", "0.12.85", "0.13.85", "4.1.85"]
for d in (r.get("result") or {}).get("data", []):
    model = d.get("model") or ""
    if "gateway" in model: continue
    v = {x["resourceId"]: x.get("value") for x in ((call("query.resource.value", {"resources": [{"subjectId": d["did"], "resourceIds": RES}]}, access).get("result")) or [])}
    num = lambda k, div=1: (float(v[k]) / div) if v.get(k) not in (None, "") else None
    name = d.get("deviceName") or model
    site = "Drivhus Oslo"  # alle Aqara-enheter står i drivhuset i Oslo
    on = None if v.get("4.1.85") in (None, "") else v["4.1.85"] == "1"
    con.execute("insert into aqara_readings (did,name,model,site,temp,hum,power,energy,on_off) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (d["did"], name, model, site, num("0.1.85", 100), num("0.2.85", 100), num("0.12.85"), num("0.13.85", 1000), on))
f = lambda x: None if x is None else float(x)
out = {}
for did, n, m, s, ts, t, h, p, e, o in con.execute("select did,name,model,site,ts,temp,hum,power,energy,on_off from aqara_readings where ts > now() - interval '30 days' order by ts").fetchall():
    x = out.setdefault(did, {"name": n, "model": m, "site": s, "rows": []})
    x["name"], x["site"] = n, s
    x["rows"].append({"t": ts.isoformat(), "temp": f(t), "hum": f(h), "power": f(p), "kwh": f(e), "on": o})
NAMES = {"lumi.54ef441000760552": "Lys drivhus", "lumi.54ef441000a0c5c6": "Varme drivhus",
         "lumi.54ef441000efb484": "Ventilasjon drivhus", "lumi.158d000253f9f4": "Drivhus Oslo"}
for did, x in out.items():
    x["name"] = NAMES.get(did, x["name"]); x["did"] = did
OUT["devices"] = list(out.values()); done()
