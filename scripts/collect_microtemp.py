"""Henter gulvvarmetermostater fra Microtemp/OJ Electronics-skyen (uoffisielt API), lagrer i Neon og skriver site/microtemp.json."""
import json, os, sys
import requests, psycopg

U, P, K = (os.environ.get(x) for x in ("OJ_USERNAME", "OJ_PASSWORD", "OJ_API_KEY"))
if not (U and P and K):
    print("OJ_USERNAME/OJ_PASSWORD/OJ_API_KEY mangler, hopper over Microtemp"); sys.exit(0)
CUST = int(os.environ.get("OJ_CUSTOMER_ID") or 15)
HOST = os.environ.get("OJ_HOST") or "owd5-mh015-app.ojelectronics.com"
SITE = "Oslo"
OUT = {"error": None, "customer": CUST, "thermostats": []}

def done():
    os.makedirs("site", exist_ok=True)
    json.dump(OUT, open("site/microtemp.json", "w"))

def val(d, *keys):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None

try:
    r = requests.post(f"https://{HOST}/api/UserProfile/SignIn", timeout=30,
                      json={"APIKEY": K, "ClientSWVersion": 1, "CustomerId": CUST, "UserName": U, "Password": P}).json()
    if r.get("ErrorCode") not in (0, None) or not r.get("SessionId"):
        OUT["error"] = f"Innlogging avvist (ErrorCode {r.get('ErrorCode')}). Sjekk kunde-ID, brukernavn og passord."
        done(); print(OUT["error"]); sys.exit(0)
    sid = r["SessionId"]; g = {}; tried = []
    for method, path in (("GET", "/api/Group/GetGroupContents"), ("POST", "/api/Group/GetGroupContents"), ("GET", "/api/Group/GetGroups"), ("POST", "/api/Group/GetGroups")):
        q = {"sessionid": sid, "APIKEY": K}
        resp = requests.request(method, f"https://{HOST}{path}", timeout=30, params=q if method == "GET" else None,
                                json=None if method == "GET" else {"SessionId": sid, "APIKEY": K})
        try:
            j = resp.json()
        except Exception:
            j = {}
        tried.append(f"{method} {path.split('/')[-1]}: HTTP {resp.status_code} " + (str(j.get("Message") or j.get("MessageDetail") or sorted(j.keys()))[:120] if isinstance(j, dict) else "liste"))
        if resp.status_code == 200 and isinstance(j, dict) and "GroupContents" in j:
            g = j; break
    OUT["tried"] = tried
    if not g:
        OUT["error"] = "Fant ikke riktig endepunkt for termostatlisten."; done(); print(OUT["error"]); sys.exit(0)
    if g.get("ErrorCode") not in (0, None):
        OUT["error"] = f"Henting feilet (ErrorCode {g.get('ErrorCode')})"; done(); print(OUT["error"]); sys.exit(0)
    gc = g.get("GroupContents") or []
    OUT["debug"] = {"top": sorted(g.keys()), "groups": len(gc), "group_keys": sorted(gc[0].keys()) if gc and isinstance(gc[0], dict) else None}
    for k, v in (gc[0].items() if gc and isinstance(gc[0], dict) else []):
        if isinstance(v, list) and v and isinstance(v[0], dict):
            OUT["debug"]["list_" + k] = {"n": len(v), "keys": sorted(v[0].keys())}
    found = []
    for grp in g.get("GroupContents") or []:
        for t in (grp.get("Thermostats") or grp.get("Thermostat") or grp.get("Devices") or []):
            c = lambda x: None if x is None else round(float(x) / 100, 1)
            found.append({"serial": str(val(t, "SerialNumber", "Id")), "name": val(t, "ThermostatName", "Name") or grp.get("GroupName"),
                          "room": c(val(t, "RoomTemperature")), "floor": c(val(t, "FloorTemperature")),
                          "set": c(val(t, "SetPointTemp", "ManualModeSetpoint", "ComfortSetpoint")),
                          "heating": bool(val(t, "Heating")), "mode": val(t, "RegulationMode"), "online": val(t, "Online")})
    with psycopg.connect(os.environ["NEON_DATABASE_URL"]) as con:
        con.execute("""create table if not exists microtemp_readings (ts timestamptz not null default now(), serial text, name text,
            room numeric, floor numeric, setpoint numeric, heating boolean, mode int)""")
        con.execute("create index if not exists microtemp_ts on microtemp_readings (ts)")
        for t in found:
            con.execute("insert into microtemp_readings (serial,name,room,floor,setpoint,heating,mode) values (%s,%s,%s,%s,%s,%s,%s)",
                        (t["serial"], t["name"], t["room"], t["floor"], t["set"], t["heating"], t["mode"]))
        out = {}
        f = lambda x: None if x is None else float(x)
        for s, n, ts, ro, fl, st, he, mo in con.execute("select serial,name,ts,room,floor,setpoint,heating,mode from microtemp_readings where ts > now() - interval '30 days' order by ts").fetchall():
            d = out.setdefault(s, {"name": n, "site": SITE, "rows": []})
            d["rows"].append({"t": ts.isoformat(), "room": f(ro), "floor": f(fl), "set": f(st), "heat": he, "mode": mo})
    OUT["thermostats"] = list(out.values())
    if not found:
        OUT["error"] = "Innlogging OK, men ingen termostater i svaret."
    print("Microtemp: termostater", len(found))
except Exception as e:
    OUT["error"] = "Feil: " + type(e).__name__
    print(OUT["error"])
done()
