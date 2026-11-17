"""Henter status fra Zaptec-laderen, lagrer i Neon og skriver site/zaptec.json."""
import json, os, sys
import requests, psycopg

U, P = os.environ.get("ZAPTEC_USERNAME"), os.environ.get("ZAPTEC_PASSWORD")
if not U or not P:
    print("ZAPTEC_USERNAME/ZAPTEC_PASSWORD mangler, hopper over Zaptec"); sys.exit(0)
SITE = "Arendal"
B = "https://api.zaptec.com"
t = requests.post(B + "/oauth/token", data={"grant_type": "password", "username": U, "password": P}, timeout=30)
if t.status_code != 200:
    raise RuntimeError("Zaptec-innlogging feilet: HTTP %s" % t.status_code)
H = {"Authorization": "Bearer " + t.json()["access_token"]}
chs = requests.get(B + "/api/chargers", headers=H, timeout=30).json().get("Data") or []
def num(x):
    try: return float(x)
    except Exception: return None
rows = []
for c in chs:
    st = requests.get(f"{B}/api/chargers/{c['Id']}/state", headers=H, timeout=30).json()
    s = {x["StateId"]: x.get("ValueAsString") for x in st}
    rows.append((c["Id"], c.get("Name") or c.get("DeviceId"), num(s.get(-2)), num(s.get(710)), num(s.get(513)), num(s.get(553)),
                 num(s.get(501)), num(s.get(507)), num(s.get(508)), num(s.get(509))))
with psycopg.connect(os.environ["NEON_DATABASE_URL"]) as con:
    con.execute("""create table if not exists zaptec_readings (ts timestamptz not null default now(), charger_id text, name text,
        online numeric, mode numeric, power_w numeric, session_kwh numeric, v1 numeric, i1 numeric, i2 numeric, i3 numeric)""")
    con.execute("create index if not exists zaptec_ts on zaptec_readings (ts)")
    for r in rows:
        con.execute("insert into zaptec_readings (charger_id,name,online,mode,power_w,session_kwh,v1,i1,i2,i3) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", r)
    out = {}
    for cid, name, ts, on, mo, pw, se in con.execute("select charger_id,name,ts,online,mode,power_w,session_kwh from zaptec_readings where ts > now() - interval '30 days' order by ts").fetchall():
        d = out.setdefault(cid, {"name": name, "site": SITE, "rows": []})
        f = lambda x: None if x is None else float(x)
        d["rows"].append({"t": ts.isoformat(), "on": f(on), "m": f(mo), "w": f(pw), "kwh": f(se)})
os.makedirs("site", exist_ok=True)
json.dump({"chargers": list(out.values())}, open("site/zaptec.json", "w"))
print("Zaptec: ladere", len(rows))
