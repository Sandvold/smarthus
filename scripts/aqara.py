"""Aqara Cloud: be om kode, hent token (lagres i Neon) og list enheter. Bruk: aqara.py code | token <kode> | devices"""
import hashlib, json, os, sys, time, uuid, requests, psycopg

APP_ID = os.environ["AQARA_APP_ID"].strip(); KEY_ID = os.environ["AQARA_KEY_ID"].strip()
APP_KEY = os.environ["AQARA_APP_KEY"].strip(); ACCOUNT = os.environ["AQARA_ACCOUNT"].strip()
REGION = os.environ.get("AQARA_REGION", "open-ger").strip()
URL = f"https://{REGION}.aqara.com/v3.0/open/api"

def call(intent, data, token=None):
    h = {"Appid": APP_ID, "Keyid": KEY_ID, "Nonce": uuid.uuid4().hex, "Time": str(int(time.time() * 1000))}
    if token: h["Accesstoken"] = token
    s = "&".join(f"{k}={h[k]}" for k in sorted(h, key=str.lower)) + "&" + APP_KEY
    h["Sign"] = hashlib.md5(s.lower().encode()).hexdigest()
    h["Content-Type"] = "application/json"
    return requests.post(URL, headers=h, json={"intent": intent, "data": data}, timeout=30).json()

def db():
    con = psycopg.connect(os.environ["NEON_DATABASE_URL"])
    con.execute("create table if not exists aqara_auth (id int primary key, access text, refresh text, updated timestamptz default now())")
    return con

def show(r):
    print(json.dumps(r, ensure_ascii=False)[:3000])

cmd = sys.argv[1]
if cmd == "code":
    show(call("config.auth.getAuthCode", {"account": ACCOUNT, "accountType": 0, "accessTokenValidity": "7d"}))
elif cmd == "token":
    r = call("config.auth.getToken", {"authCode": sys.argv[2], "account": ACCOUNT, "accountType": 0})
    res = r.get("result") or {}
    if res.get("accessToken"):
        with db() as con:
            con.execute("insert into aqara_auth (id,access,refresh) values (1,%s,%s) on conflict (id) do update set access=excluded.access, refresh=excluded.refresh, updated=now()", (res["accessToken"], res["refreshToken"]))
        print("Token lagret. code:", r.get("code"))
    else:
        show(r)
elif cmd == "devices":
    with db() as con:
        row = con.execute("select access from aqara_auth where id=1").fetchone()
    if not row: sys.exit("Ingen token lagret")
    r = call("query.device.info", {"pageNum": 1, "pageSize": 50}, row[0])
    for d in (r.get("result") or {}).get("data", []):
        print(d.get("did"), "|", d.get("deviceName"), "|", d.get("model"), "|", "online" if d.get("state") == 1 else "offline", "| pos:", d.get("positionId"))
    if r.get("code") != 0: show(r)
