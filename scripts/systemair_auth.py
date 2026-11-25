"""Innlogging mot Systemair Homesolutions (samme flyt som nettsiden). Returnerer (token, årsak)."""
import re, html as H
import requests
from urllib.parse import urlparse, parse_qs, urlencode
TOKEN_URL = "https://sso.systemair.com/auth/realms/iot/protocol/openid-connect/token"

def login(EMAIL, PW):
    r = requests.post(TOKEN_URL, data={"grant_type": "password", "username": EMAIL, "password": PW, "client_id": "iot-application", "scope": "openid email profile"}, timeout=30)
    j = r.json() if r.content else {}
    tok = j.get("access_token"); why = str(j.get("error_description") or j.get("error") or r.status_code)[:80]
    if not tok:  # samme innlogging som nettsiden bruker (skjema + kode)
        import re, html as H
        from urllib.parse import urlparse, parse_qs, urlencode
        ses = requests.Session(); RED = "https://homesolutions.systemair.com"
        au = "https://sso.systemair.com/auth/realms/iot/protocol/openid-connect/auth?" + urlencode({"client_id": "iot-application", "redirect_uri": RED, "response_type": "code", "scope": "openid", "state": "abc123"})
        p1 = ses.get(au, timeout=30); m = re.search(r'id="kc-form-login"[^>]*action="([^"]+)"', p1.text) or re.search(r'action="([^"]+)"[^>]*id="kc-form-login"', p1.text)
        if m:
            loc = ses.post(H.unescape(m.group(1)), data={"username": EMAIL, "password": PW, "rememberMe": "on", "credentialId": ""}, allow_redirects=False, timeout=30)
            for _ in range(12):
                u = loc.headers.get("Location", "")
                if u.startswith(RED) or not u: break
                loc = ses.get(u, allow_redirects=False, timeout=30)
            code = (parse_qs(urlparse(loc.headers.get("Location", "")).query).get("code") or [None])[0]
            _l = urlparse(loc.headers.get("Location", ""))
            print("skjema-innlogging:", "fikk kode" if code else f"ingen kode (HTTP {loc.status_code})", "| mål:", _l.netloc + _l.path, "| parametre:", sorted(parse_qs(_l.query)), "| fragment:", bool(_l.fragment))
            if code:
                t2 = ses.post(TOKEN_URL, data={"grant_type": "authorization_code", "client_id": "iot-application", "code": code, "redirect_uri": RED}, timeout=30)
                j2 = t2.json() if t2.content else {}
                tok = j2.get("access_token")
                if not tok: print("kodebytte feilet: HTTP", t2.status_code, str(j2.get("error"))[:60], str(j2.get("error_description"))[:120]); why = "kodebytte: " + str(j2.get("error_description") or j2.get("error"))[:80]
        else:
            print("fant ikke innloggingsskjema, HTTP", p1.status_code)
    return tok, why
