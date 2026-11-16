"""Henter målinger fra Tuya-skyen, lagrer i Neon og skriver site/data.json."""
import hashlib, hmac, json, os, time, uuid
import requests, psycopg

HOST = os.environ.get("TUYA_HOST") or "https://openapi.tuyaeu.com"
CID, SECRET = os.environ["TUYA_ACCESS_ID"], os.environ["TUYA_ACCESS_SECRET"]
EMPTY_SHA = hashlib.sha256(b"").hexdigest()

def sign(token, t, nonce, method, path):
    string_to_sign = f"{method}\n{EMPTY_SHA}\n\n{path}"
    msg = CID + token + t + nonce + string_to_sign
    return hmac.new(SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest().upper()

def call(path, token=""):
    t, nonce = str(int(time.time() * 1000)), uuid.uuid4().hex
    headers = {"client_id": CID, "t": t, "nonce": nonce, "sign_method": "HMAC-SHA256",
               "sign": sign(token, t, nonce, "GET", path)}
    if token:
        headers["access_token"] = token
    r = requests.get(HOST + path, headers=headers, timeout=30).json()
    if not r.get("success"):
        raise RuntimeError(f"Tuya-feil på {path}: {r}")
    return r["result"]

def main():
    token = call("/v1.0/token?grant_type=1")["access_token"]
    devices = json.load(open("config/devices.json"))
    rows = []
    for d in devices:
        props = call(f"/v2.0/cloud/thing/{d['id']}/shadow/properties", token)["properties"]
        p = {x["code"]: x["value"] for x in props}
        temp = p.get("temp_current")
        rows.append((d["id"], d["name"], d["site"],
                     None if temp is None else temp / d.get("temp_scale", 10),
                     p.get("humidity_value"), p.get("battery_percentage")))
    with psycopg.connect(os.environ["NEON_DATABASE_URL"]) as con:
        con.execute("""create table if not exists readings (
            ts timestamptz not null default now(), device_id text not null, name text,
            site text, temp numeric, hum numeric, battery numeric)""")
        con.execute("create index if not exists readings_ts on readings (ts)")
        for r in rows:
            con.execute("insert into readings (device_id,name,site,temp,hum,battery) values (%s,%s,%s,%s,%s,%s)", r)
        out = con.execute("""select ts, name, site, temp, hum, battery from readings
            where ts > now() - interval '14 days' order by ts""").fetchall()
    os.makedirs("site", exist_ok=True)
    json.dump([{"ts": a.isoformat(), "name": b, "site": c,
                "temp": None if d is None else float(d),
                "hum": None if e is None else float(e),
                "battery": None if f is None else float(f)} for a, b, c, d, e, f in out],
              open("site/data.json", "w"))

if __name__ == "__main__":
    main()
