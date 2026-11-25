"""Lister Adax-rom og -enheter (til oppsett). Skriver ut navn, temperaturer og rå JSON, aldri passord."""
import json, os, requests
API = "https://api-1.adax.no/client-api"
t = requests.post(API + "/auth/token", data={"grant_type": "password", "username": os.environ["ADAX_ACCOUNT_ID"].strip(), "password": os.environ["ADAX_PASSWORD"].strip()}, timeout=30)
print("token HTTP", t.status_code)
if t.status_code != 200:
    print(t.text[:300]); raise SystemExit(1)
h = {"Authorization": "Bearer " + t.json()["access_token"]}
r = requests.get(API + "/rest/v1/content/", headers=h, timeout=30)
print("content HTTP", r.status_code)
j = r.json()
print(json.dumps(j, ensure_ascii=False)[:3500])

print("--- energi ---")
rooms = j.get("rooms") or []
if rooms:
    print("rom-nøkler:", sorted(rooms[0].keys()))
for k in ("devices", "energy"):
    if j.get(k) is not None: print(k, json.dumps(j[k], ensure_ascii=False)[:800])
import time
rid = rooms[0]["id"] if rooms else None
if rid:
    now = int(time.time() * 1000); frm = now - 2 * 86400000
    for path in (f"/rest/v1/energy_log/{rid}", f"/rest/v1/energy_log/{rid}?fromTime={frm}&toTime={now}", f"/rest/v1/energy/{rid}", f"/rest/v1/content/?withEnergy=true"):
        try:
            q = requests.get(API + path, headers=h, timeout=30)
            print(path.split("?")[0], "HTTP", q.status_code, q.text[:500])
        except Exception as e:
            print(path, "feil", type(e).__name__)
