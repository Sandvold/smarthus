"""Salus Test: kjører trinnvis mot Salus Open API og skriver site/salustest.json (vises i «Salus Test» på siden).
Trinn 1-4 er kun lesing. Skriving (trinn 5) skjer bare når WRITE_DEVICE og WRITE_SETPOINT er satt og endepunktet er kjent (WRITE_PATH)."""
import json, os, re, sys, datetime
import requests

E, P = (os.environ.get("SALUS_EMAIL") or "").strip(), (os.environ.get("SALUS_PASSWORD") or "").strip()
BASE = os.environ.get("SALUS_HOST") or "https://openapi-eu.salusconnect.io"
OUT = {"updated": datetime.datetime.now(datetime.timezone.utc).isoformat(), "steps": [], "devices": [], "api": {}, "write": {"status": "venter", "detail": "Ikke kjørt. Venter på godkjenning før noe skrives til termostatene."}}
STEPS = ["Logge inn", "Finne gateway", "Finne termostater", "Lese temperatur og settpunkt", "Finne endepunkt for å sette temperatur", "Skrive til én termostat"]

def clean(s):
    s = str(s)
    for v in (E, P):
        if v: s = s.replace(v, "<skjult>")
    return s[:300]

def rec(i, status, detail=""):
    while len(OUT["steps"]) <= i:
        OUT["steps"].append({"n": len(OUT["steps"]) + 1, "name": STEPS[len(OUT["steps"])], "status": "venter", "detail": ""})
    OUT["steps"][i].update(status=status, detail=clean(detail))

def save():
    os.makedirs("site", exist_ok=True)
    json.dump(OUT, open("site/salustest.json", "w"), ensure_ascii=False)

for i in range(len(STEPS)): rec(i, "venter")
if not (E and P):
    rec(0, "feil", "SALUS_EMAIL/SALUS_PASSWORD mangler i GitHub-secrets"); save(); sys.exit(0)

def sc(x):
    n = float(x); return n / 100 if abs(n) > 100 else n

try:
    r = requests.post(BASE + "/login", json={"email": E, "password": P}, timeout=30)
    tok = (r.json() or {}).get("access_token") if r.ok else None
    if not tok:
        rec(0, "feil", f"Innlogging avvist (HTTP {r.status_code}): {r.text[:120]}"); save(); sys.exit(0)
    rec(0, "ok", "Innlogget mot Salus Open API")
    H = {"Authorization": "Bearer " + tok}
    get = lambda p, **k: requests.get(BASE + p, headers=H, timeout=30, **k)

    gws = get("/premium-lite/gateways").json()
    glist = gws if isinstance(gws, list) else (gws.get("gateways") or gws.get("data") or [])
    rec(1, "ok" if glist else "feil", ", ".join(str(g.get("name")) for g in glist) or "ingen gatewayer")
    OUT["gateways"] = [{"code": g.get("device_code"), "name": g.get("name")} for g in glist]

    devs = []
    for g in glist:
        dv = get(f"/premium-lite/gateways/{g.get('device_code')}/devices").json()
        dl = dv if isinstance(dv, list) else (dv.get("devices") or dv.get("data") or [])
        for d in dl:
            dc = d if isinstance(d, str) else (d.get("device_code") or d.get("code"))
            det = get(f"/premium-lite/devices/{dc}").json()
            devs.append((g, dc, det))
    th = [x for x in devs if str(x[2].get("model", "")).startswith("SQ610")]
    rec(2, "ok" if th else "feil", f"{len(devs)} enheter, {len(th)} termostater (SQ610)" + ("" if len(th) == 8 else f" · du forventet 8"))

    t1 = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1); t0 = t1 - datetime.timedelta(days=2)
    nread = 0
    for g, dc, det in devs:
        row = {"code": dc, "name": det.get("name"), "model": det.get("model"), "connected": det.get("connected"), "gateway": g.get("name"), "temp": None, "setpoint": None, "at": None}
        try:
            hr = get(f"/premium-lite/history/{dc}?start_date={t0:%Y-%m-%d}&end_date={t1:%Y-%m-%d}&order=desc&limit=5").json()
            pr = hr.get("properties") or {}
            for k, f in (("current_temperature", "temp"), ("heating_setpoint", "setpoint")):
                recs = (pr.get(k) or {}).get("records") or []
                if recs:
                    row[f] = sc(recs[0]["value"]); row["at"] = row["at"] or recs[0].get("at")
            if row["temp"] is not None: nread += 1
        except Exception as e:
            row["feil"] = type(e).__name__
        OUT["devices"].append(row)
    rec(3, "ok" if nread else "feil", f"Leste temperatur fra {nread} av {len(th)} termostater")
    save()

    # Finn API-spesifikasjonen (kun GET) og se hvilke metoder enhetsendepunktet tillater
    api = OUT["api"]
    cands = []
    try:
        html = requests.get(BASE + "/docs/", timeout=30).text
        cands += [u if u.startswith("http") else BASE + (u if u.startswith("/") else "/docs/" + u) for u in re.findall(r"""["']([^"']+\.(?:json|ya?ml))["']""", html)]
    except Exception:
        pass
    cands += [BASE + p for p in ("/docs/swagger.json", "/docs/openapi.json", "/openapi.json", "/swagger.json", "/docs.json", "/api-docs", "/docs/doc.json", "/docs/swagger.yaml")]
    seen = set(); paths = {}
    for u in cands:
        if u in seen: continue
        seen.add(u)
        try:
            rr = requests.get(u, headers=H, timeout=30)
            if not rr.ok or "paths" not in rr.text[:200000]: continue
            spec = rr.json()
            api["spec_url"] = u
            for p, ms in (spec.get("paths") or {}).items():
                if "premium" in p.lower():
                    paths[p] = {m.upper(): (v.get("summary") or v.get("operationId") or "") for m, v in ms.items() if isinstance(v, dict)}
            break
        except Exception:
            continue
    api["paths"] = paths
    dc0 = devs[0][1] if devs else None
    if dc0:
        for p in (f"/premium-lite/devices/{dc0}", f"/premium-lite/devices/{dc0}/settings", f"/premium-lite/devices/{dc0}/properties"):
            try:
                o = requests.options(BASE + p, headers=H, timeout=30)
                api.setdefault("options", {})[p.replace(dc0, "{kode}")] = {"http": o.status_code, "allow": o.headers.get("Allow") or o.headers.get("Access-Control-Allow-Methods") or ""}
            except Exception as e:
                api.setdefault("options", {})[p.replace(dc0, "{kode}")] = {"feil": type(e).__name__}
    writes = [f"{m} {p}" for p, ms in paths.items() for m in ms if m in ("POST", "PUT", "PATCH")]
    if paths:
        rec(4, "ok", f"{len(paths)} endepunkt funnet i {api.get('spec_url')}" + (f" · skrivbare: {', '.join(writes)[:200]}" if writes else " · ingen skrivbare"))
    else:
        rec(4, "feil", "Fant ingen offentlig API-spesifikasjon. Trenger endepunktet fra Salus-dokumentasjonen.")

    # Trinn 6: skriving, kun når alt er eksplisitt satt av eieren
    wd, wv, wp = os.environ.get("WRITE_DEVICE", "").strip(), os.environ.get("WRITE_SETPOINT", "").strip(), os.environ.get("WRITE_PATH", "").strip()
    if wd and wv and wp:
        tgt = next((x for x in devs if x[1] == wd or x[2].get("name") == wd), None)
        if not tgt:
            OUT["write"] = {"status": "feil", "detail": "Fant ikke termostaten " + wd}
        else:
            body = json.loads(os.environ.get("WRITE_BODY") or '{"heating_setpoint": %d}' % int(round(float(wv) * 100)))
            url = BASE + wp.replace("{kode}", tgt[1])
            meth = (os.environ.get("WRITE_METHOD") or "POST").upper()
            rr = requests.request(meth, url, headers=H, json=body, timeout=30)
            OUT["write"] = {"status": "ok" if rr.ok else "feil", "detail": f"{meth} {wp} -> HTTP {rr.status_code}: {clean(rr.text[:150])}", "device": tgt[2].get("name"), "value": wv}
        rec(5, OUT["write"]["status"], OUT["write"]["detail"])
    else:
        rec(5, "venter", "Venter på godkjenning fra deg")
except Exception as e:
    for s in OUT["steps"]:
        if s["status"] == "venter":
            s["status"] = "feil"; s["detail"] = "Stoppet: " + type(e).__name__ + ": " + clean(e); break
save()
print(json.dumps([{k: s[k] for k in ("n", "name", "status", "detail")} for s in OUT["steps"]], ensure_ascii=False, indent=1))
print("enheter:", [(d["name"], d["temp"], d["setpoint"]) for d in OUT["devices"]])
print("api-stier:", json.dumps(OUT["api"], ensure_ascii=False)[:1500])
