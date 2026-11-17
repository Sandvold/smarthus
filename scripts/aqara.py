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
print("AppID-kontroll (tegn adskilt):", " ".join(APP_ID[:6]), "...", " ".join(APP_ID[-4:]), "| lengde", len(APP_ID), "| KeyID", " ".join(KEY_ID[:4]), "..", " ".join(KEY_ID[-4:]), "| region", REGION)
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

if cmd == "diag":
    import itertools
    for region, kid in itertools.product(["open-sg", "open-ger", "open-usa", "open-kr", "open-ru", "open-cn"], [KEY_ID, KEY_ID.removeprefix("K.")]):
        URL = f"https://{region}.aqara.com/v3.0/open/api"
        KEY_ID_USED = kid
        h = {"Appid": APP_ID, "Keyid": kid, "Nonce": uuid.uuid4().hex, "Time": str(int(time.time() * 1000))}
        s = "&".join(f"{k}={h[k]}" for k in sorted(h)) + "&" + APP_KEY
        h["Sign"] = hashlib.md5(s.lower().encode()).hexdigest(); h["Content-Type"] = "application/json"
        try:
            r = requests.post(URL, headers=h, json={"intent": "config.auth.getAuthCode", "data": {"account": ACCOUNT, "accountType": 0, "accessTokenValidity": "7d"}}, timeout=20).json()
        except Exception as e:
            r = {"err": str(e)[:80]}
        print(region, "keyid-prefix" if kid == KEY_ID else "keyid-uten-K.", r.get("code"), r.get("messageDetail") or r.get("message") or r.get("err"))
    print("lengder: appid", len(APP_ID), "keyid", len(KEY_ID), "appkey", len(APP_KEY), "keyid starter K.:", KEY_ID.startswith("K."))
