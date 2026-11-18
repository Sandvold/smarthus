"""Henter Gardena-sensorer og ventiler, lagrer i Neon og skriver site/gardena.json."""
import json, os, sys, requests, psycopg
ACCOUNTS = [(os.environ.get("GARDENA_CLIENT_ID", "").strip(), os.environ.get("GARDENA_CLIENT_SECRET", "").strip()),
            (os.environ.get("GARDENA_CLIENT_ID_2", "").strip(), os.environ.get("GARDENA_CLIENT_SECRET_2", "").strip())]
ACCOUNTS = [a for a in ACCOUNTS if a[0] and a[1]]
if not ACCOUNTS:
    print("Gardena-secrets mangler, hopper over"); sys.exit(0)
B = "https://api.smart.gardena.dev/v1"
devs = {}
SITES = json.load(open("config/gardena.json")).get("sites", [])
for idx, (cid, sec) in enumerate(ACCOUNTS):
    site_name = SITES[idx] if idx < len(SITES) else None
    t = requests.post("https://api.authentication.husqvarnagroup.dev/v1/oauth2/token",
        data={"grant_type": "client_credentials", "client_id": cid, "client_secret": sec}, timeout=30)
    if t.status_code != 200:
        print("Gardena-konto feilet, HTTP", t.status_code); continue
    h = {"Authorization": "Bearer " + t.json()["access_token"], "X-Api-Key": cid, "Authorization-Provider": "husqvarna", "Content-Type": "application/vnd.api+json"}
    for loc in requests.get(B + "/locations", headers=h, timeout=30).json().get("data", []):
        for x in requests.get(f"{B}/locations/{loc['id']}", headers=h, timeout=30).json().get("included", []):
            a = {k: v.get("value") for k, v in x.get("attributes", {}).items() if isinstance(v, dict) and "value" in v}
            d = devs.setdefault(x["id"], {"site": site_name})
            if x["type"] == "COMMON": d.update(name=a.get("name"), model=a.get("modelType"), batt=a.get("batteryLevel"), link=a.get("rfLinkState"))
            elif x["type"] == "SENSOR": d.update(kind="sensor", soil_hum=a.get("soilHumidity"), soil_temp=a.get("soilTemperature"))
            elif x["type"] == "VALVE": d.update(kind="valve", activity=a.get("activity"), vname=a.get("name"))
with psycopg.connect(os.environ["NEON_DATABASE_URL"]) as con:
    con.execute("""create table if not exists gardena_readings (ts timestamptz not null default now(), device_id text,
        name text, kind text, site text, soil_hum numeric, soil_temp numeric, activity text, battery int, link text)""")
    # Flerventil-styring (id "<enhet>:1", ":2"): COMMON ligger på enhets-id uten suffiks, og ventilen har eget navn
    for did, d in devs.items():
        if d.get("kind") != "valve" or ":" not in did: continue
        base = devs.get(did.split(":")[0]) or {}
        for k in ("batt", "link", "model"):
            if d.get(k) is None: d[k] = base.get(k)
        if not d.get("name"):
            vn, bn = d.get("vname"), base.get("name")
            d["name"] = (bn + " – " + vn if bn and vn and vn != bn else vn or (bn and bn + " ventil " + did.split(":")[1]) or "Ventil " + did.split(":")[1])
    for did, d in devs.items():
        if "kind" not in d: continue
        con.execute("insert into gardena_readings (device_id,name,kind,site,soil_hum,soil_temp,activity,battery,link) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (did, d.get("name"), d["kind"], d.get("site"), d.get("soil_hum"), d.get("soil_temp"), d.get("activity"), d.get("batt"), d.get("link")))
    for did, d in devs.items():
        if d.get("name"): con.execute("update gardena_readings set name=%s where device_id=%s and name is null", (d["name"], did))
    for did, d in devs.items():
        con.execute("update gardena_readings set site=%s where device_id=%s and site is distinct from %s", (d.get("site"), did, d.get("site")))
    rows = con.execute("select device_id,name,kind,ts,soil_hum,soil_temp,activity,battery,link from gardena_readings where ts > now() - interval '14 days' order by ts").fetchall()
out = {}
for did, name, kind, ts, sh, st, act, b, link in rows:
    if not name: continue
    d = out.setdefault(did, {"id": did, "name": name, "kind": kind, "site": (devs.get(did) or {}).get("site"), "rows": []})
    d["rows"].append({"t": ts.isoformat(), "hum": None if sh is None else float(sh), "temp": None if st is None else float(st), "act": act, "batt": b, "link": link})
json.dump({"devices": list(out.values())}, open("site/gardena.json", "w"), ensure_ascii=False)
print("Gardena: enheter", len(out), "rader", len(rows))
