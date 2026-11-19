"""Henter bildata (ID.4) fra VW Group EU Data Act-portalen og skriver site/vw.json.
Trenger VW_USERNAME og VW_PASSWORD (GitHub-hemmeligheter). Skriver ikke VIN eller brukernavn til loggen (offentlig repo)."""
import json, os, sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
import psycopg
from vw_euda.client import EudaApiClient, ApiError, AuthError, TooManyRequestsError, NO_CONTENT_SUFFIX

OUT = {"error": None, "rows": [], "updated": None}
FIELDS = {  # kolonne -> feltnavn i datasettet
    "soc": "battery_level_HV.value", "cs": "charging_state_report.current_charge_state",
    "kw": "battery_state_report.charge_power", "outdoor": "outdoor_temperature",
    "tmin": "min_temperature", "tmax": "max_temperature", "locked": "locked",
    "km": "mileage.value", "clim": "climatisation_state", "tsoc": "settings.target_soc",
}

def done(msg=None):
    OUT["error"] = msg; OUT["updated"] = datetime.now(timezone.utc).isoformat()
    os.makedirs("site", exist_ok=True); json.dump(OUT, open("site/vw.json", "w")); print("VW:", msg or f"{len(OUT['rows'])} målinger")

def num(x):
    try: return float(x)
    except (TypeError, ValueError): return None

def parse_ts(x):
    try: return datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    except ValueError: return None

def extract(payload, fallback):
    """Plukker ut feltene vi bruker. Tidspunkt = nyeste car_captured_time i filen (ellers filens createdOn)."""
    vals, times = {}, []
    for d in payload.get("Data") or []:
        n = d.get("dataFieldName"); v = d.get("value")
        if n == "car_captured_time":
            t = parse_ts(v)
            if t: times.append(t)
        for col, f in FIELDS.items():
            if n == f and col not in vals: vals[col] = v
    ts = max(times) if times else fallback
    return ts, {
        "soc": num(vals.get("soc")), "cs": vals.get("cs"), "kw": num(vals.get("kw")), "outdoor": num(vals.get("outdoor")),
        "tmin": num(vals.get("tmin")), "tmax": num(vals.get("tmax")),
        "locked": None if vals.get("locked") is None else str(vals["locked"]).lower() == "true",
        "km": num(vals.get("km")), "clim": vals.get("clim"), "tsoc": num(vals.get("tsoc"))}

if __name__ == "__main__":
    user, pw = (os.environ.get(k, "").strip() for k in ("VW_USERNAME", "VW_PASSWORD"))
    if not user or not pw: print("VW-hemmeligheter mangler"); sys.exit(0)
    con = psycopg.connect(os.environ["NEON_DATABASE_URL"], autocommit=True)
    con.execute("""create table if not exists vw_readings (file text primary key, ts timestamptz not null, soc numeric, cs text, kw numeric,
      outdoor numeric, tmin numeric, tmax numeric, locked boolean, km numeric, clim text, tsoc numeric)""")
    try:
        c = EudaApiClient(user, pw, country=os.environ.get("VW_COUNTRY", "no"), language=os.environ.get("VW_LANGUAGE", "nb"),
                          brand=os.environ.get("VW_BRAND", "VOLKSWAGEN_PASSENGER_CARS"))
        c.login()
        vehicles = c.list_vehicles()
        if not vehicles: done("Ingen biler funnet på portalen."); sys.exit(0)
        vin = vehicles[0]["vin"]
        ident = c.get_metadata(vin, "partial").get("Identifier") or ""
        files = c.list_datasets(vin, ident, "partial")
        seen = {r[0] for r in con.execute("select file from vw_readings").fetchall()}
        new = [f for f in files if f.get("name") and f["name"] not in seen]
        new.sort(key=lambda f: f.get("createdOn") or f["name"])
        got = 0
        for f in new:
            name = f["name"]
            if name.endswith(NO_CONTENT_SUFFIX):
                con.execute("insert into vw_readings (file, ts) values (%s, %s) on conflict do nothing", (name, parse_ts(f.get("createdOn")) or datetime.now(timezone.utc))); continue
            ts, r = extract(c.download_dataset(vin, ident, name, "partial"), parse_ts(f.get("createdOn")) or datetime.now(timezone.utc))
            con.execute("insert into vw_readings (file,ts,soc,cs,kw,outdoor,tmin,tmax,locked,km,clim,tsoc) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict do nothing",
                        (name, ts, r["soc"], r["cs"], r["kw"], r["outdoor"], r["tmin"], r["tmax"], r["locked"], r["km"], r["clim"], r["tsoc"])); got += 1
        print(f"VW: {len(files)} filer i portalen, {got} nye med data")
    except (AuthError, ApiError, TooManyRequestsError) as e:
        msg = str(e).replace(user, "***").replace(pw, "***")[:200]
        print("VW-feil:", type(e).__name__, msg)
        err = f"{type(e).__name__}: {msg}"
    else:
        err = None
    for ts, soc, cs, kw, out, tmin, tmax, lk, km, cl, ts_ in con.execute(
            "select distinct on (ts) ts,soc,cs,kw,outdoor,tmin,tmax,locked,km,clim,tsoc from vw_readings where soc is not null and ts > now() - interval '7 days' order by ts").fetchall():
        f = lambda x: None if x is None else float(x)
        OUT["rows"].append({"t": ts.isoformat(), "soc": f(soc), "cs": cs, "kw": f(kw), "out": f(out), "tmin": f(tmin), "tmax": f(tmax), "lock": lk, "km": f(km), "clim": cl, "tsoc": f(ts_)})
    done(err)
