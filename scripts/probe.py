"""Skriver ut status og styrbare funksjoner for én Tuya-enhet (til feilsøking)."""
import json, os, sys
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("NEON_DATABASE_URL", "unused")
from collect import call

dev = sys.argv[1]
token = call("/v1.0/token?grant_type=1")["access_token"]
for title, path in [
    ("INFO", f"/v1.0/devices/{dev}"),
    ("STATUS", f"/v2.0/cloud/thing/{dev}/shadow/properties"),
    ("FUNKSJONER", f"/v1.0/devices/{dev}/functions"),
    ("SPESIFIKASJON", f"/v1.0/devices/{dev}/specifications"),
]:
    try:
        r = call(path, token)
        if title == "INFO":
            r = {k: r.get(k) for k in ("name", "category", "product_name", "online", "sub", "model")}
        print("==", title); print(json.dumps(r, ensure_ascii=False, indent=1))
    except Exception as e:
        print("==", title, "FEIL", e)
