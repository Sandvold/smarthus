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
