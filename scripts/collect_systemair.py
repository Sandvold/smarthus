"""Henter Systemair SAVE-ventilasjon via Homesolutions-skyen (uoffisielt GraphQL, OIDC passordinnlogging). Skriver site/systemair.json."""
import json, os, sys
import requests, psycopg

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
    r = requests.post(TOKEN_URL, data={"grant_type": "password", "username": EMAIL, "password": PW, "client_id": "iot-application", "scope": "openid email profile"}, timeout=30)
    j = r.json() if r.content else {}
    tok = j.get("access_token"); why = str(j.get("error_description") or j.get("error") or r.status_code)[:80]
    if not tok:  # samme innlogging som nettsiden bruker (skjema + kode)
        import re, html as H
        from urllib.parse import urlparse, parse_qs, urlencode
        ses = requests.Session(); RED = "https://homesolutions.systemair.com"
        au = "https://sso.systemair.com/auth/realms/iot/protocol/openid-connect/auth?" + urlencode({"client_id": "iot-application", "redirect_uri": RED, "response_type": "code", "scope": "openid", "state": "abc123"})
        p1 = ses.get(au, timeout=30); m = re.search(r'id="kc-form-login"[^>]*action="([^"]+)"', p1.text) or re.search(r'action="([^"]+)"[^>]*id="kc-form-login"', p1.text)
        if m:
            loc = ses.post(H.unescape(m.group(1)), data={"username": EMAIL, "password": PW, "rememberMe": "on", "credentialId": ""}, allow_redirects=False, timeout=30)
            for _ in range(12):
                u = loc.headers.get("Location", "")
                if u.startswith(RED) or not u: break
                loc = ses.get(u, allow_redirects=False, timeout=30)
            code = (parse_qs(urlparse(loc.headers.get("Location", "")).query).get("code") or [None])[0]
            _l = urlparse(loc.headers.get("Location", ""))
            print("skjema-innlogging:", "fikk kode" if code else f"ingen kode (HTTP {loc.status_code})", "| mål:", _l.netloc + _l.path, "| parametre:", sorted(parse_qs(_l.query)), "| fragment:", bool(_l.fragment))
            if code:
                t2 = ses.post(TOKEN_URL, data={"grant_type": "authorization_code", "client_id": "iot-application", "code": code, "redirect_uri": RED}, timeout=30)
                j2 = t2.json() if t2.content else {}
                tok = j2.get("access_token")
                if not tok: print("kodebytte feilet: HTTP", t2.status_code, str(j2.get("error"))[:60], str(j2.get("error_description"))[:120]); why = "kodebytte: " + str(j2.get("error_description") or j2.get("error"))[:80]
        else:
            print("fant ikke innloggingsskjema, HTTP", p1.status_code)
    if not tok:
        OUT["error"] = "Innlogging avvist: " + why + " (passord, eller kontoen bruker Google/Apple?)"
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
