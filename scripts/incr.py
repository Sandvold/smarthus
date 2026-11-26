"""Inkrementell eksport: bruk forrige site/*.json (fra Actions-cache) og hent bare nye rader fra Neon.
Sparer utgående trafikk (Neon gratisplan: 5 GB/mnd). Faller tilbake til full spørring hvis filen mangler,
er eldre enn 36 timer, mangler id-felt, eller FULL_EXPORT er satt."""
import datetime as dt, json, os

def _now():
    return dt.datetime.now(dt.timezone.utc)

def prev_entities(path, listkey, idfn, maxage_h=36):
    """-> (dict id->entitet, siste tidspunkt) eller ({}, None) for full spørring."""
    if os.environ.get("FULL_EXPORT"):
        return {}, None
    try:
        ents = json.load(open(path))[listkey]
        got = {idfn(e): e for e in ents}
        if None in got:
            return {}, None
        since = None
        for e in ents:
            for r in e.get("rows", []):
                t = dt.datetime.fromisoformat(r["t"])
                if since is None or t > since:
                    since = t
        if since is None or _now() - since > dt.timedelta(hours=maxage_h):
            return {}, None
        return got, since
    except Exception:
        return {}, None

def merge(out, prev, days):
    """Legg nye rader (out) etter gamle rader (prev) som fortsatt er innenfor vinduet."""
    cutoff = (_now() - dt.timedelta(days=days)).isoformat()
    for k, e in prev.items():
        old = [r for r in e.get("rows", []) if r["t"] >= cutoff]
        if k in out:
            out[k]["rows"] = old + out[k]["rows"]
        elif old:
            e["rows"] = old
            out[k] = e
    return out

def prev_list(path, maxage_h=36):
    """For lister av poster med 'ts' (data.json): -> (liste, siste tidspunkt) eller ([], None)."""
    if os.environ.get("FULL_EXPORT"):
        return [], None
    try:
        lst = json.load(open(path))
        since = max(dt.datetime.fromisoformat(r["ts"]) for r in lst)
        if _now() - since > dt.timedelta(hours=maxage_h):
            return [], None
        return lst, since
    except Exception:
        return [], None

def window(days):
    return _now() - dt.timedelta(days=days)
