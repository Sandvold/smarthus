"""Anslår effekt og kWh for varmepumpen i Arendal ut fra kompressorfrekvens, utetemperatur og vanntemperatur.
Pumpen sender ikke strøm/effekt til Tuya, så modellen kalibreres mot avlesninger fra produsentens app (tabell hp_calib).
Skriver site/hp_power.json."""
import json, os, sys, datetime
from zoneinfo import ZoneInfo
import psycopg

url = os.environ.get("NEON_DATABASE_URL")
if not url:
    print("NEON_DATABASE_URL mangler"); sys.exit(0)
STANDBY_W = 15.0      # kompressor står: appen viser 0,0 A (under ca. 24 W), resten er antakelse
MAX_GAP_MIN = 30      # lengre hull i målingene teller ikke som drift
DEFAULT_PF = 0.95     # antakelse til watt = V*A*pf

def solve(A, b):
    n = len(b); M = [r[:] + [b[i]] for i, r in enumerate(A)]
    for i in range(n):
        p = max(range(i, n), key=lambda r: abs(M[r][i])); M[i], M[p] = M[p], M[i]
        if abs(M[i][i]) < 1e-9: return None
        for r in range(n):
            if r != i:
                f = M[r][i] / M[i][i]
                M[r] = [x - f * y for x, y in zip(M[r], M[i])]
    return [M[i][n] / M[i][i] for i in range(n)]

def feats(kind, hz, amb, tw):
    if kind == "prop": return [hz]
    if kind == "lin": return [1.0, hz]
    if kind == "lin_amb": return [1.0, hz, amb]
    return [1.0, hz, amb, tw]

def fit(pts):
    n = len(pts)
    kind = "prop" if n < 3 else "lin" if n < 8 else "lin_amb" if n < 14 else "lin_amb_tw"
    X = [feats(kind, p["hz"], p["amb"], p["tw"]) for p in pts]; y = [p["w"] for p in pts]
    k = len(X[0]); lam = 1e-6
    A = [[sum(r[i] * r[j] for r in X) + (lam if i == j else 0) for j in range(k)] for i in range(k)]
    b = [sum(r[i] * yy for r, yy in zip(X, y)) for i in range(k)]
    c = solve(A, b)
    if c is None: kind, c = "prop", [sum(y) / max(1.0, sum(p["hz"] for p in pts))]
    return kind, c

def predict(kind, c, hz, amb, tw):
    return max(0.0, sum(a * b for a, b in zip(c, feats(kind, hz, amb, tw))))

with psycopg.connect(url) as con:
    con.execute("create table if not exists hp_calib (id serial primary key, ts timestamptz not null default now(), hz numeric, amb numeric, t_in numeric, t_out numeric, volt numeric, amp numeric, pf numeric default 0.95, note text)")
    cal = con.execute("select ts,hz,amb,t_in,t_out,volt,amp,coalesce(pf,%s),note from hp_calib order by ts", (DEFAULT_PF,)).fetchall()
    rd = con.execute("select ts, props from readings where kind='heatpump' and site='Arendal' and ts > now() - interval '35 days' order by ts").fetchall()

pts = [dict(ts=r[0].isoformat(), hz=float(r[1]), amb=float(r[2]), tw=float(r[3]), tout=None if r[4] is None else float(r[4]),
            v=float(r[5]), a=float(r[6]), w=float(r[5]) * float(r[6]) * float(r[7]), note=r[8]) for r in cal if r[1] is not None and r[5] is not None]
out = {"updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "n": len([p for p in pts if p["hz"] > 0]), "standby_w": STANDBY_W, "pf": DEFAULT_PF}
if not pts:
    out["model"] = None
else:
    run_pts = [p for p in pts if p["hz"] > 0]  # stillstand brukes ikke i tilpasningen, den håndteres av STANDBY_W
    kind, c = fit(run_pts) if run_pts else ("prop", [25.0])
    res = [predict(kind, c, p["hz"], p["amb"], p["tw"]) - p["w"] for p in pts]
    rr = [e for e, p in zip(res, pts) if p["hz"] > 0] or [0.0]
    rmse = (sum(e * e for e in rr) / len(rr)) ** 0.5
    mean_w = sum(p["w"] for p in run_pts) / max(1, len(run_pts))
    out["model"] = {"kind": kind, "coef": [round(x, 4) for x in c], "rmse_w": round(rmse, 1), "rel_pct": round(100 * rmse / mean_w, 1) if mean_w else None}
    for p, e in zip(pts, res):
        p["est"] = round(p["w"] + e) if p["hz"] > 0 else round(STANDBY_W); p["w"] = round(p["w"])

    def est(p):
        hz = p.get("compressor_strength"); run = p.get("compressor_state") is True and isinstance(hz, (int, float)) and hz > 0
        if not run: return STANDBY_W
        amb = p.get("around_temp"); tw = p.get("temp_bottom")
        return predict(kind, c, float(hz), float(amb if isinstance(amb, (int, float)) else 0), float(tw if isinstance(tw, (int, float)) else 40))
    series, daily, prev = [], {}, None
    tz = ZoneInfo("Europe/Oslo")
    for ts, props in rd:
        p = props if isinstance(props, dict) else json.loads(props)
        w = est(p)
        if prev is not None:
            dt = (ts - prev[0]).total_seconds() / 60
            if 0 < dt <= MAX_GAP_MIN:
                d = ts.astimezone(tz).date().isoformat()
                daily[d] = daily.get(d, 0.0) + (prev[1] + w) / 2 * dt / 60 / 1000
        prev = (ts, w)
        if ts > datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=8):
            series.append([int(ts.timestamp() * 1000), round(w)])
    out["now_w"] = round(prev[1]) if prev else None
    out["series"] = series
    out["daily"] = [{"d": d, "kwh": round(v, 2)} for d, v in sorted(daily.items())]
out["calib"] = pts
os.makedirs("site", exist_ok=True)
json.dump(out, open("site/hp_power.json", "w"), ensure_ascii=False)
print("hp_power:", out.get("model"), "punkter", len(pts), "now_w", out.get("now_w"))
