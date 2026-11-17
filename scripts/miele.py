"""Miele 3rd Party API. Kommandoer: token <kode> <redirect_uri>  |  collect"""
import json, os, sys
import requests, psycopg

CID, SEC = os.environ.get("MIELE_CLIENT_ID"), os.environ.get("MIELE_CLIENT_SECRET")
VG = os.environ.get("MIELE_VG") or "nb-NO"
SITE = "Oslo"
B = "https://api.mcs3.miele.com"
if not (CID and SEC):
    print("MIELE_CLIENT_ID/SECRET mangler, hopper over Miele"); sys.exit(0)
con = psycopg.connect(os.environ["NEON_DATABASE_URL"], autocommit=True)
con.execute("create table if not exists miele_auth (id int primary key, access text, refresh text, updated timestamptz default now())")

def save(j):
    con.execute("insert into miele_auth (id,access,refresh) values (1,%s,%s) on conflict (id) do update set access=excluded.access, refresh=excluded.refresh, updated=now()", (j["access_token"], j["refresh_token"]))

def tok(data):
    r = requests.post(B + "/thirdparty/token", data={**data, "client_id": CID, "client_secret": SEC, "vg": VG}, timeout=30)
    try: return r.status_code, r.json()
    except Exception: return r.status_code, {"raw": r.text[:200]}

cmd = sys.argv[1] if len(sys.argv) > 1 else "collect"
if cmd == "token":
    import re
    code = sys.argv[2].strip()
    m = re.search(r"(?:^|[?&#])code=([^&\s]+)", code)
    if m: code = m.group(1)
    if "client_id=" in code or code.startswith("http"):
        print("Feltet «code» inneholder en lenke uten code=. Lim inn adressen du havnet på ETTER innlogging."); sys.exit(1)
    sc, j = tok({"grant_type": "authorization_code", "code": code, "redirect_uri": sys.argv[3]})
    if sc != 200 or "access_token" not in j:
        print("Miele token feilet: HTTP", sc, str(j)[:200]); sys.exit(1)
    save(j); print("Miele: tilgang lagret"); sys.exit(0)

OUT = {"error": None, "devices": []}
def done():
    os.makedirs("site", exist_ok=True); json.dump(OUT, open("site/miele.json", "w"))
row = con.execute("select refresh from miele_auth where id=1").fetchone()
if not row:
    OUT["error"] = "Miele er ikke godkjent ennå."; done(); print(OUT["error"]); sys.exit(0)
sc, j = tok({"grant_type": "refresh_token", "refresh_token": row[0]})
if sc != 200 or "access_token" not in j:
    OUT["error"] = f"Miele-tilgangen er utløpt eller avvist (HTTP {sc}). Godkjenn på nytt."; done(); print(OUT["error"]); sys.exit(0)
save(j)
r = requests.get(B + "/v1/devices", headers={"Authorization": "Bearer " + j["access_token"], "Accept": "application/json"}, params={"language": "nb"}, timeout=30)
if r.status_code != 200:
    OUT["error"] = f"Henting av enheter feilet (HTTP {r.status_code})"; done(); print(OUT["error"]); sys.exit(0)
def num(x):
    try: return float(x)
    except Exception: return None
def first(lst, key):
    return num(lst[0].get(key)) if isinstance(lst, list) and lst and isinstance(lst[0], dict) and lst[0].get(key) not in (None, -32768) else None
rows = []
for did, d in (r.json() or {}).items():
    ident = d.get("ident") or {}; st = d.get("state") or {}
    typ = (ident.get("type") or {}).get("value_localized") or ""
    name = ident.get("deviceName") or typ or did
    status = (st.get("status") or {}).get("value_localized") or ""
    rows.append((did, name, typ, status, first(st.get("temperature"), "value_localized"), first(st.get("targetTemperature"), "value_localized"), bool(st.get("signalDoor"))))
con.execute("""create table if not exists miele_readings (ts timestamptz not null default now(), device_id text, name text, type text, status text,
    temp numeric, target numeric, door boolean)""")
con.execute("create index if not exists miele_ts on miele_readings (ts)")
for x in rows:
    con.execute("insert into miele_readings (device_id,name,type,status,temp,target,door) values (%s,%s,%s,%s,%s,%s,%s)", x)
out = {}; f = lambda v: None if v is None else float(v)
for did, n, t, ts, s, tp, tg, dr in con.execute("select device_id,name,type,ts,status,temp,target,door from miele_readings where ts > now() - interval '30 days' order by ts").fetchall():
    o = out.setdefault(did, {"name": n, "type": t, "site": SITE, "rows": []})
    o["rows"].append({"t": ts.isoformat(), "status": s, "temp": f(tp), "target": f(tg), "door": dr})
OUT["devices"] = list(out.values()); done(); print("Miele: enheter", len(rows))
