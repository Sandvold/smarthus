"""Leser (kun lesing) Systemair-visninger og skriver ut dataelementer: id, register, skrivbar, grenser, valg. Ingen skriving."""
import json, os, sys
import requests
sys.path.insert(0, os.path.dirname(__file__))
from systemair_auth import login
tok, why = login((os.environ.get("SYSTEMAIR_EMAIL") or "").strip(), (os.environ.get("SYSTEMAIR_PASSWORD") or "").strip())
if not tok: print("innlogging feilet", why); raise SystemExit(1)
A = "https://homesolutions.systemair.com/gateway/"
d = requests.post(A + "api", json={"query": "{ GetAccountDevices { identifier } }"}, headers={"x-access-token": tok}, timeout=60).json()["data"]["GetAccountDevices"][0]["identifier"]
H = {"x-access-token": tok, "device-id": d, "device-type": "TELTONIKA"}
VIEW = "query GetView($input: GetViewInputType!) { GetView(input: $input) { id title route children { type properties feature } error } }"
def walk(o, out):
    if isinstance(o, dict):
        di = o.get("dataItem")
        if isinstance(di, dict):
            ex = di.get("extension") or {}
            out.append({"id": di.get("id"), "reg": ex.get("modbusRegister"), "type": di.get("type"), "ro": di.get("readOnly"), "min": di.get("min"), "max": di.get("max"), "step": di.get("step"), "val": di.get("value"), "opt": [(x.get("title"), x.get("value")) for x in (di.get("options") or [])][:14]})
        for v in o.values(): walk(v, out)
    elif isinstance(o, list):
        for v in o: walk(v, out)
seen = set()
for route in ["/home", "/home/change_mode", "/change_mode", "/mode", "/user_mode", "/airflow", "/temperature", "/settings", "/week_schedule", "/filter", "/alarms", "/unit_information"]:
    r = requests.post(A + "remote-api/", json={"operationName": "GetView", "query": VIEW, "variables": {"input": {"route": route, "viewId": ""}}}, headers=H, timeout=60).json()
    v = (r.get("data") or {}).get("GetView")
    if not v or v.get("error"):
        print(route, "-> ", str((v or {}).get("error") or r.get("errors"))[:100]); continue
    items = []; walk(v.get("children"), items)
    print(route, "-> OK", v.get("title"), len(items), "elementer")
    for it in items:
        if it["id"] in seen: continue
        seen.add(it["id"]); print("  ", json.dumps(it, ensure_ascii=False)[:260])
