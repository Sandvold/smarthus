import { createHash, randomUUID } from 'node:crypto';

// Bare disse tre pluggene kan styres.
export const PLUGS: Record<string, string> = {
  'lumi.54ef441000760552': 'Lys drivhus',
  'lumi.54ef441000a0c5c6': 'Varme drivhus',
  'lumi.54ef441000efb484': 'Ventilasjon drivhus',
};
const allowed = (process.env.ALLOWED_EMAILS || '').toLowerCase().split(',').map(s => s.trim()).filter(Boolean);
const origins = (process.env.ALLOWED_ORIGINS || '').split(',').map(s => s.trim()).filter(Boolean);
const issuer = process.env.NEON_AUTH_BASE_URL ? new URL(process.env.NEON_AUTH_BASE_URL).origin : '';
const b64u = (s: string) => Uint8Array.from(atob(s.replace(/-/g, '+').replace(/_/g, '/')), c => c.charCodeAt(0));
let keys: any[] = []; let keysAt = 0;

async function getKeys(force = false) {
  if (!force && keys.length && Date.now() - keysAt < 600000) return keys;
  keys = ((await (await fetch(process.env.NEON_AUTH_JWKS_URL!)).json()) as any).keys || []; keysAt = Date.now(); return keys;
}
async function verify(token: string): Promise<any | null> {
  const [h, p, s] = token.split('.');
  if (!h || !p || !s) return null;
  let head: any, payload: any;
  try { head = JSON.parse(new TextDecoder().decode(b64u(h))); payload = JSON.parse(new TextDecoder().decode(b64u(p))); } catch { return null; }
  if (head.alg !== 'EdDSA') return null;
  let k = (await getKeys()).find(x => x.kid === head.kid) || (await getKeys(true)).find(x => x.kid === head.kid);
  if (!k) return null;
  const key = await crypto.subtle.importKey('jwk', { kty: k.kty, crv: k.crv, x: k.x }, 'Ed25519', false, ['verify']);
  if (!(await crypto.subtle.verify('Ed25519', key, b64u(s), new TextEncoder().encode(h + '.' + p)))) return null;
  if (!payload.exp || payload.exp * 1000 < Date.now()) return null;
  if (payload.iss && new URL(payload.iss).origin !== issuer) return null;
  return payload;
}
export async function sql(query: string, params: unknown[] = []) {
  const u = new URL(process.env.DATABASE_URL!);
  const r = await fetch(`https://${u.host}/sql`, {
    method: 'POST', headers: { 'Neon-Connection-String': process.env.DATABASE_URL!, 'content-type': 'application/json' },
    body: JSON.stringify({ query, params }),
  });
  if (!r.ok) throw new Error('sql ' + r.status);
  return ((await r.json()) as any).rows as any[];
}

type Cfg = { appId: string; keyId: string; appKey: string; url: string; access: string; refresh: string };
async function loadCfg(): Promise<Cfg> {
  const s: Record<string, string> = {};
  for (const r of await sql('select name, value from ctl_secrets')) s[r.name] = r.value;
  const a = (await sql('select access, refresh from aqara_auth where id = 1'))[0];
  if (!s.AQARA_APP_ID || !s.AQARA_KEY_ID || !s.AQARA_APP_KEY || !a) throw new Error('mangler oppsett');
  return { appId: s.AQARA_APP_ID, keyId: s.AQARA_KEY_ID, appKey: s.AQARA_APP_KEY, url: `https://${s.AQARA_REGION || 'open-sg'}.aqara.com/v3.0/open/api`, access: a.access, refresh: a.refresh };
}
async function aqara(c: Cfg, intent: string, data: unknown, useToken = true): Promise<any> {
  const h: Record<string, string> = { Appid: c.appId, Keyid: c.keyId, Nonce: randomUUID().replace(/-/g, ''), Time: String(Date.now()) };
  if (useToken) h.Accesstoken = c.access;
  const str = Object.keys(h).sort((x, y) => x.toLowerCase().localeCompare(y.toLowerCase())).map(k => `${k}=${h[k]}`).join('&') + c.appKey;
  h.Sign = createHash('md5').update(str.toLowerCase()).digest('hex');
  h['Content-Type'] = 'application/json';
  return (await fetch(c.url, { method: 'POST', headers: h, body: JSON.stringify({ intent, data }) })).json();
}
async function call(c: Cfg, intent: string, data: unknown): Promise<any> {
  let r = await aqara(c, intent, data);
  if (r.code === 108 || r.code === 107) {
    const t = await aqara(c, 'config.auth.refreshToken', { refreshToken: c.refresh });
    if (t.result?.accessToken) {
      c.access = t.result.accessToken; c.refresh = t.result.refreshToken || c.refresh;
      await sql('update aqara_auth set access = $1, refresh = $2, updated = now() where id = 1', [c.access, c.refresh]);
      r = await aqara(c, intent, data);
    }
  }
  return r;
}
export async function plugStates(c?: Cfg, only?: string | null) {
  c = c || await loadCfg();
  const out: Record<string, { name: string; on: boolean | null; power: number | null; code: number }> = {};
  for (const [did, name] of Object.entries(PLUGS)) {
    if (only && did !== only) continue;
    const r = await call(c, 'query.resource.value', { resources: [{ subjectId: did, resourceIds: ['4.1.85', '0.12.85'] }] });
    const rs = r.result || [];
    const v = rs.find((x: any) => x.resourceId === '4.1.85')?.value;
    const w = rs.find((x: any) => x.resourceId === '0.12.85')?.value;
    out[did] = { name, on: v == null || v === '' ? null : v === '1', power: w == null || w === '' || isNaN(Number(w)) ? null : Number(w), code: r.code };
    if (r.code === 0 && out[did].on != null) {
      try {
        await sql('create table if not exists plug_state (did text primary key, name text, on_state boolean, power double precision, ts timestamptz not null default now())');
        await sql('insert into plug_state (did, name, on_state, power, ts) values ($1,$2,$3,$4,now()) on conflict (did) do update set name=$2, on_state=$3, power=$4, ts=now()', [did, name, out[did].on, out[did].power]);
      } catch {}
    }
  }
  return out;
}
async function setPlug(did: string, on: boolean) {
  const c = await loadCfg();
  const r = await call(c, 'write.resource.device', [{ subjectId: did, resources: [{ resourceId: '4.1.85', value: on ? '1' : '0' }] }]);
  return { code: r.code, message: r.messageDetail || r.message || null };
}

// Låser terrassedøren (Yale) via en Homey-webhook. Kun "lås" – aldri opplåsing herfra.
async function lockTerrasse(email: string): Promise<[unknown, number]> {
  await sql('create table if not exists ctl_log (ts timestamptz not null default now(), email text, did text, action text, code int, message text)');
  const url = (await sql("select value from ctl_secrets where name = 'HOMEY_LOCK_URL'"))[0]?.value;
  if (!url) return [{ error: 'Homey-lås er ikke satt opp ennå' }, 503];
  const recent = await sql("select 1 from ctl_log where did = 'terrasse' and ts > now() - interval '10 seconds' limit 1");
  if (recent.length) return [{ error: 'for raskt, vent noen sekunder' }, 429];
  let code = 0, msg: string | null = null;
  try {
    const r = await fetch(url, { method: 'GET', signal: AbortSignal.timeout(10000) });
    code = r.ok ? 0 : r.status; msg = r.ok ? 'Success' : 'HTTP ' + r.status;
  } catch { code = -1; msg = 'ingen kontakt med Homey'; }
  await sql('insert into ctl_log (email, did, action, code, message) values ($1,$2,$3,$4,$5)', [email, 'terrasse', 'lås', code, msg]);
  return code === 0 ? [{ ok: true, did: 'terrasse', action: 'lås' }, 200] : [{ error: 'Homey svarte ikke: ' + msg }, 502];
}

function cors(req: Request): Record<string, string> {
  const o = req.headers.get('origin') || '';
  const h: Record<string, string> = { 'Access-Control-Allow-Headers': 'authorization, content-type', 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS', 'Vary': 'Origin' };
  if (origins.includes(o)) h['Access-Control-Allow-Origin'] = o;
  return h;
}
const json = (req: Request, body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { ...cors(req), 'content-type': 'application/json', 'cache-control': 'no-store' } });

export default {
  async fetch(req: Request) {
    if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors(req) });
    if (req.method !== 'GET' && req.method !== 'POST') return json(req, { error: 'method' }, 405);
    const auth = req.headers.get('authorization') || '';
    if (!auth.toLowerCase().startsWith('bearer ')) return json(req, { error: 'unauthorized' }, 401);
    let payload: any;
    try { payload = await verify(auth.slice(7)); } catch { payload = null; }
    if (!payload) return json(req, { error: 'unauthorized' }, 401);
    let email: string | null = typeof payload.email === 'string' ? payload.email.toLowerCase() : null;
    if (!email && payload.sub) email = (await sql('select email from neon_auth."user" where id = $1', [payload.sub]))[0]?.email?.toLowerCase() ?? null;
    if (!email || !allowed.includes(email)) return json(req, { error: 'forbidden' }, 403);
    try {
      if (req.method === 'GET') {
        const q = new URL(req.url).searchParams;
        if (q.get('cached')) {
          let rows: any[] = [];
          try { rows = await sql('select did, name, on_state, power, ts from plug_state'); } catch {}
          const plugs: Record<string, unknown> = {};
          for (const r of rows) plugs[r.did] = { name: r.name, on: r.on_state, power: r.power, ts: r.ts };
          return json(req, { plugs });
        }
        return json(req, { plugs: await plugStates(undefined, q.get('did')) });
      }
      let b: any; try { b = await req.json(); } catch { return json(req, { error: 'ugyldig' }, 400); }
      if (b.lock === 'terrasse') { const [body, st] = await lockTerrasse(email); return json(req, body, st); }
      if (typeof b.did !== 'string' || !(b.did in PLUGS) || typeof b.on !== 'boolean') return json(req, { error: 'ugyldig enhet eller verdi' }, 400);
      await sql('create table if not exists ctl_log (ts timestamptz not null default now(), email text, did text, action text, code int, message text)');
      const recent = await sql("select 1 from ctl_log where did = $1 and ts > now() - interval '3 seconds' limit 1", [b.did]);
      if (recent.length) return json(req, { error: 'for raskt, vent noen sekunder' }, 429);
      const res = await setPlug(b.did, b.on);
      await sql('insert into ctl_log (email, did, action, code, message) values ($1,$2,$3,$4,$5)', [email, b.did, b.on ? 'på' : 'av', res.code, res.message]);
      if (res.code !== 0) return json(req, { error: 'Aqara svarte med kode ' + res.code, message: res.message }, 502);
      return json(req, { ok: true, did: b.did, on: b.on });
    } catch (e: any) {
      return json(req, { error: 'feil: ' + String(e?.message || e).slice(0, 80) }, 500);
    }
  },
};
