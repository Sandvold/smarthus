"""Henter gatewayer og enheter fra Salus Open API (kun lesing) og skriver site/salus.json. Skriver også en kort oversikt over feltene i loggen."""
import json, os, sys
import requests

E, P = (os.environ.get("SALUS_EMAIL") or "").strip(), (os.environ.get("SALUS_PASSWORD") or "").strip()
if not (E and P):
    print("SALUS_EMAIL/SALUS_PASSWORD mangler, hopper over Salus"); sys.exit(0)
BASE = os.environ.get("SALUS_HOST") or "https://openapi-eu.salusconnect.io"
OUT = {"error": None, "gateways": []}

def done():
    os.makedirs("site", exist_ok=True)
    json.dump(OUT, open("site/salus.json", "w"))

def shape(x, depth=0):
    if isinstance(x, dict):
        return {k: shape(v, depth + 1) if depth < 3 else type(v).__name__ for k, v in list(x.items())[:40]}
    if isinstance(x, list):
        return [shape(x[0], depth + 1)] if x else []
    return type(x).__name__

try:
    r = requests.post(BASE + "/login", json={"email": E, "password": P}, timeout=30)
    tok = (r.json() or {}).get("access_token") if r.ok else None
    if not tok:
        OUT["error"] = f"Innlogging avvist (HTTP {r.status_code}): " + r.text[:160].replace(E, "<e-post>").replace(P, "<passord>"); done(); print(OUT["error"]); sys.exit(0)
    H = {"Authorization": "Bearer " + tok}
    get = lambda p: requests.get(BASE + p, headers=H, timeout=30)
    gws = get("/premium-lite/gateways").json()
    print("gateways-form:", json.dumps(shape(gws)))
    glist = gws if isinstance(gws, list) else (gws.get("gateways") or gws.get("data") or gws.get("items") or [])
    first = True
    for g in glist:
        gc = g.get("gateway_code") or g.get("code") or g.get("id")
        dv = get(f"/premium-lite/gateways/{gc}/devices").json()
        dl = dv if isinstance(dv, list) else (dv.get("devices") or dv.get("data") or dv.get("items") or [])
        gout = {"code": gc, "info": g, "devices": []}
        for d in dl:
            dc = d if isinstance(d, str) else (d.get("device_code") or d.get("code") or d.get("id"))
            det = get(f"/premium-lite/devices/{dc}").json()
            st = get(f"/premium-lite/status/{dc}").json()
            if first:
                print("enhet-form:", json.dumps(shape(det))); print("status-form:", json.dumps(shape(st))); first = False
            gout["devices"].append({"code": dc, "details": det, "status": st})
        OUT["gateways"].append(gout)
    print("Salus: gatewayer", len(OUT["gateways"]), "enheter", sum(len(x["devices"]) for x in OUT["gateways"]))
except Exception as e:
    OUT["error"] = "Feil: " + type(e).__name__
    print(OUT["error"])
done()
