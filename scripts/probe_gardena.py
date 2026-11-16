"""Lister Gardena-lokasjoner og enheter (til oppsett). Skriver aldri ut nøkler."""
import json, os, requests
cid, sec = os.environ["GARDENA_CLIENT_ID"].strip(), os.environ["GARDENA_CLIENT_SECRET"].strip()
t = requests.post("https://api.authentication.husqvarnagroup.dev/v1/oauth2/token",
    data={"grant_type": "client_credentials", "client_id": cid, "client_secret": sec}, timeout=30)
print("token HTTP", t.status_code)
if t.status_code != 200:
    print(t.text[:300]); raise SystemExit(1)
h = {"Authorization": "Bearer " + t.json()["access_token"], "X-Api-Key": cid, "Authorization-Provider": "husqvarna", "Content-Type": "application/vnd.api+json"}
B = "https://api.smart.gardena.dev/v1"
r = requests.get(B + "/locations", headers=h, timeout=30)
print("locations HTTP", r.status_code)
if r.status_code != 200:
    print(r.text[:400]); raise SystemExit(1)
for loc in r.json().get("data", []):
    print("== lokasjon", loc["id"], loc["attributes"].get("name"))
    d = requests.get(f"{B}/locations/{loc['id']}", headers=h, timeout=30)
    print("detaljer HTTP", d.status_code)
    for x in d.json().get("included", []):
        a = x.get("attributes", {})
        vals = {k: v.get("value") for k, v in a.items() if isinstance(v, dict) and "value" in v}
        print(x["type"], x["id"][:8], json.dumps(vals, ensure_ascii=False)[:400])
