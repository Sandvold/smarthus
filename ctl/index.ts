import { createHash, randomUUID } from 'node:crypto';

// Bare disse tre pluggene kan styres.
export const PLUGS: Record<string, string> = {
  'lumi.54ef441000760552': 'Lys drivhus',
  'lumi.54ef441000a0c5c6': 'Ventilasjon drivhus',
  'lumi.54ef441000efb484': 'Varme drivhus',
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
async function lockTerrasse(email: string, unlock = false): Promise<[unknown, number]> {
  await sql('create table if not exists ctl_log (ts timestamptz not null default now(), email text, did text, action text, code int, message text)');
  const url = (await sql('select value from ctl_secrets where name = $1', [unlock ? 'HOMEY_UNLOCK_URL' : 'HOMEY_LOCK_URL']))[0]?.value;
  if (!url) return [{ error: unlock ? 'Opplåsing er ikke satt opp ennå' : 'Homey-lås er ikke satt opp ennå' }, 503];
  const recent = await sql("select 1 from ctl_log where did = 'terrasse' and ts > now() - interval '10 seconds' limit 1");
  if (recent.length) return [{ error: 'for raskt, vent noen sekunder' }, 429];
  let code = 0, msg: string | null = null;
  try {
    const m = url.match(/^https:\/\/webhook\.homey\.app\/([^/]+)\/([^/?]+)/);
    const get = (u: string) => fetch(u, { method: 'GET', signal: AbortSignal.timeout(4000) }).then(x => x.status).catch(() => -1);
    // Eldre Homey Pro (2016–2019, 24-tegns ID) nås via connect.athom.com. Prøv den først.
    const old = m && /^[0-9a-f]{24}$/.test(m[1]) ? await get(`https://${m[1]}.connect.athom.com/api/manager/logic/webhook/${m[2]}?tag=web`) : null;
    const nw = old !== null ? null : await get(url);
    const ok = (old !== null && old >= 200 && old < 300) || (old === null && nw !== null && nw >= 200 && nw < 300);
    code = ok ? 0 : (nw ?? old ?? -1);
    msg = (ok ? 'Success' : 'Feil') + ' old:' + (old ?? '-') + ' new:' + (nw ?? '-');
  } catch { code = -1; msg = 'ingen kontakt med Homey'; }
  await sql('insert into ctl_log (email, did, action, code, message) values ($1,$2,$3,$4,$5)', [email, 'terrasse', unlock ? 'lås opp' : 'lås', code, msg]);
  return code === 0 ? [{ ok: true, did: 'terrasse', action: unlock ? 'lås opp' : 'lås' }, 200] : [{ error: 'Homey svarte ikke: ' + msg }, 502];
}

// ---------- Styring av flere leverandører (alt på hvitliste, bekreftes med avlesing) ----------
const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));
async function secretsMap() { const s: Record<string, string> = {}; for (const r of await sql('select name, value from ctl_secrets')) s[r.name] = r.value; return s; }
const form = (o: Record<string, string>) => new URLSearchParams(o);
type Out = [Record<string, unknown>, number];

// Shelly (Stikk oppvarmet drivhus): kun kanalene fra config/shelly.json.
const SHELLY_DEV = '98a3167b1668';
const SHELLY_CH: Record<string, string> = { '0': 'Vekstlys', '1': 'Ventilasjonsvifte', '2': 'Uttak 3', '3': 'Uttak 4' };
function shellyBase(s: Record<string, string>) { const v = (s.SHELLY_SERVER || '').trim().replace(/\/$/, ''); return v ? (v.startsWith('http') ? v : 'https://' + v) : ''; }
async function shellyOutput(s: Record<string, string>, ch: string): Promise<{ on: boolean; power: number | null } | null> {
  const r: any = await (await fetch(shellyBase(s) + '/device/all_status', { method: 'POST', body: form({ auth_key: s.SHELLY_AUTH_KEY }), signal: AbortSignal.timeout(8000) })).json();
  const sw = r?.data?.devices_status?.[SHELLY_DEV]?.['switch:' + ch];
  return typeof sw?.output === 'boolean' ? { on: sw.output, power: typeof sw.apower === 'number' ? sw.apower : null } : null;
}
async function actShelly(b: any): Promise<Out> {
  const ch = String(b.ch);
  if (!(ch in SHELLY_CH) || typeof b.on !== 'boolean') return [{ error: 'ugyldig kanal eller verdi' }, 400];
  const s = await secretsMap();
  if (!s.SHELLY_AUTH_KEY || !shellyBase(s)) return [{ error: 'Shelly er ikke satt opp ennå (kjør Hent målinger)' }, 503];
  let r: any = await (await fetch(shellyBase(s) + '/device/relay/control', { method: 'POST', body: form({ id: SHELLY_DEV, channel: ch, turn: b.on ? 'on' : 'off', auth_key: s.SHELLY_AUTH_KEY }), signal: AbortSignal.timeout(8000) })).json().catch(() => ({}));
  if (!r.isok) { // nyere API som reserve
    r = await (await fetch(shellyBase(s) + '/v2/devices/api/set/switch?auth_key=' + encodeURIComponent(s.SHELLY_AUTH_KEY), { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ id: SHELLY_DEV, channel: Number(ch), on: b.on }), signal: AbortSignal.timeout(8000) })).json().catch(() => ({}));
    if (r.isok === false || r.error) return [{ error: 'Shelly svarte: ' + JSON.stringify(r.errors || r.error || r).slice(0, 80) }, 502];
  }
  await sleep(1500);
  const now = await shellyOutput(s, ch).catch(() => null);
  if (now) { try {
    await sql('create table if not exists plug_state (did text primary key, name text, on_state boolean, power double precision, ts timestamptz not null default now())');
    await sql('insert into plug_state (did, name, on_state, power, ts) values ($1,$2,$3,$4,now()) on conflict (did) do update set name=$2, on_state=$3, power=$4, ts=now()', ['shelly:' + SHELLY_DEV + ':' + ch, SHELLY_CH[ch], now.on, now.power]);
  } catch {} }
  return [{ ok: true, name: SHELLY_CH[ch], on: b.on, state: now ? now.on : null, power: now ? now.power : null }, 200];
}

// Adax: innstilt temperatur 5–30 °C, kun rom som finnes på kontoen.
async function adaxToken(s: Record<string, string>) {
  const t: any = await (await fetch('https://api-1.adax.no/client-api/auth/token', { method: 'POST', body: form({ grant_type: 'password', username: s.ADAX_ACCOUNT_ID, password: s.ADAX_PASSWORD }), signal: AbortSignal.timeout(8000) })).json();
  return t.access_token as string | undefined;
}
async function adaxRooms(tok: string) { return ((await (await fetch('https://api-1.adax.no/client-api/rest/v1/content/', { headers: { Authorization: 'Bearer ' + tok }, signal: AbortSignal.timeout(8000) })).json()) as any).rooms as any[] || []; }
async function actAdax(b: any): Promise<Out> {
  const t = Number(b.target);
  if (!Number.isFinite(t) || t < 5 || t > 30 || Math.round(t * 2) !== t * 2) return [{ error: 'temperatur må være 5–30 °C i steg på 0,5' }, 400];
  const s = await secretsMap();
  if (!s.ADAX_ACCOUNT_ID || !s.ADAX_PASSWORD) return [{ error: 'Adax er ikke satt opp ennå (kjør Hent målinger)' }, 503];
  const tok = await adaxToken(s); if (!tok) return [{ error: 'Adax-innlogging feilet' }, 502];
  const room = (await adaxRooms(tok)).find(r => String(r.id) === String(b.room));
  if (!room) return [{ error: 'ukjent rom' }, 400];
  const r = await fetch('https://api-1.adax.no/client-api/rest/v1/control/', { method: 'POST', headers: { Authorization: 'Bearer ' + tok, 'content-type': 'application/json' }, body: JSON.stringify({ rooms: [{ id: room.id, targetTemperature: String(Math.round(t * 100)) }] }), signal: AbortSignal.timeout(8000) });
  if (!r.ok) return [{ error: 'Adax svarte HTTP ' + r.status }, 502];
  await sleep(1500);
  const now = (await adaxRooms(tok).catch(() => [])).find(x => String(x.id) === String(room.id));
  return [{ ok: true, name: room.name, target: t, state: now?.targetTemperature != null ? now.targetTemperature / 100 : null }, 200];
}

// Gardena: kun ventiler. Start vanning 1–30 min, eller stopp.
const GB = 'https://api.smart.gardena.dev/v1';
async function gardenaFind(s: Record<string, string>, id: string) {
  for (const [cid, sec] of [[s.GARDENA_CLIENT_ID, s.GARDENA_CLIENT_SECRET], [s.GARDENA_CLIENT_ID_2, s.GARDENA_CLIENT_SECRET_2]]) {
    if (!cid || !sec) continue;
    const t: any = await (await fetch('https://api.authentication.husqvarnagroup.dev/v1/oauth2/token', { method: 'POST', body: form({ grant_type: 'client_credentials', client_id: cid, client_secret: sec }), signal: AbortSignal.timeout(8000) })).json().catch(() => ({}));
    if (!t.access_token) continue;
    const h = { Authorization: 'Bearer ' + t.access_token, 'X-Api-Key': cid, 'Authorization-Provider': 'husqvarna', 'Content-Type': 'application/vnd.api+json' };
    const locs: any = await (await fetch(GB + '/locations', { headers: h, signal: AbortSignal.timeout(8000) })).json().catch(() => ({}));
    for (const l of locs.data || []) {
      const d: any = await (await fetch(`${GB}/locations/${l.id}`, { headers: h, signal: AbortSignal.timeout(8000) })).json().catch(() => ({}));
      const inc = d.included || [];
      const v = inc.find((x: any) => x.type === 'VALVE' && x.id === id);
      if (v) return { h, loc: l.id, name: inc.find((x: any) => x.type === 'COMMON' && x.id === id)?.attributes?.name?.value || id };
    }
  }
  return null;
}
async function actGardena(b: any): Promise<Out> {
  if (typeof b.id !== 'string' || !['start', 'stop'].includes(b.cmd)) return [{ error: 'ugyldig' }, 400];
  const min = Number(b.minutes);
  if (b.cmd === 'start' && !(Number.isInteger(min) && min >= 1 && min <= 30)) return [{ error: 'vanningstid må være 1–30 minutter' }, 400];
  const s = await secretsMap();
  if (!s.GARDENA_CLIENT_ID) return [{ error: 'Gardena er ikke satt opp ennå (kjør Hent målinger)' }, 503];
  const f = await gardenaFind(s, b.id); if (!f) return [{ error: 'fant ikke ventilen' }, 404];
  const attributes: any = b.cmd === 'start' ? { command: 'START_SECONDS_TO_OVERRIDE', seconds: min * 60 } : { command: 'STOP_UNTIL_NEXT_TASK' };
  const r = await fetch(`${GB}/command/${b.id}`, { method: 'PUT', headers: f.h, body: JSON.stringify({ data: { id: 'ctl-' + Date.now(), type: 'VALVE_CONTROL', attributes } }), signal: AbortSignal.timeout(8000) });
  if (!(r.status >= 200 && r.status < 300)) return [{ error: 'Gardena svarte HTTP ' + r.status }, 502];
  await sleep(2500);
  const d: any = await (await fetch(`${GB}/locations/${f.loc}`, { headers: f.h, signal: AbortSignal.timeout(8000) })).json().catch(() => ({}));
  const act = (d.included || []).find((x: any) => x.type === 'VALVE' && x.id === b.id)?.attributes?.activity?.value ?? null;
  return [{ ok: true, name: f.name, cmd: b.cmd, state: act }, 200];
}

// Zaptec: kun pause (506) og fortsett (507), med tilstandssjekk. Aldri omstart eller firmware.
async function actZaptec(b: any): Promise<Out> {
  if (typeof b.id !== 'string' || !['pause', 'resume'].includes(b.cmd)) return [{ error: 'ugyldig' }, 400];
  const s = await secretsMap();
  if (!s.ZAPTEC_USERNAME || !s.ZAPTEC_PASSWORD) return [{ error: 'Zaptec er ikke satt opp ennå (kjør Hent målinger)' }, 503];
  const t: any = await (await fetch('https://api.zaptec.com/oauth/token', { method: 'POST', body: form({ grant_type: 'password', username: s.ZAPTEC_USERNAME, password: s.ZAPTEC_PASSWORD }), signal: AbortSignal.timeout(8000) })).json().catch(() => ({}));
  if (!t.access_token) return [{ error: 'Zaptec-innlogging feilet' }, 502];
  const H = { Authorization: 'Bearer ' + t.access_token };
  const chs: any = await (await fetch('https://api.zaptec.com/api/chargers', { headers: H, signal: AbortSignal.timeout(8000) })).json().catch(() => ({}));
  const ch = (chs.Data || []).find((c: any) => c.Id === b.id); if (!ch) return [{ error: 'ukjent lader' }, 404];
  const state = async () => { const st: any = await (await fetch(`https://api.zaptec.com/api/chargers/${b.id}/state`, { headers: H, signal: AbortSignal.timeout(8000) })).json().catch(() => []); const m: Record<number, string> = {}; for (const x of st || []) m[x.StateId] = x.ValueAsString; return { mode: Number(m[710]), final: m[718] }; };
  const s0 = await state();
  if (b.cmd === 'pause' && s0.mode !== 3) return [{ error: 'Laderen lader ikke akkurat nå, så den kan ikke pauses' }, 409];
  if (b.cmd === 'resume' && !(s0.mode === 5 && s0.final === '1')) return [{ error: 'Laderen står ikke pauset av en pausekommando, så «fortsett» brukes ikke' }, 409];
  const r = await fetch(`https://api.zaptec.com/api/chargers/${b.id}/sendCommand/${b.cmd === 'pause' ? 506 : 507}`, { method: 'POST', headers: { ...H, 'content-type': 'application/json' }, signal: AbortSignal.timeout(10000) });
  if (!(r.status >= 200 && r.status < 300)) return [{ error: 'Zaptec svarte HTTP ' + r.status }, 502];
  await sleep(3000);
  const s1 = await state().catch(() => null);
  return [{ ok: true, name: ch.Name || ch.DeviceId, cmd: b.cmd, state: s1 ? s1.mode : null }, 200];
}

async function actDispatch(email: string, b: any): Promise<Out> {
  const key = b.act === 'shelly' ? 'shelly:' + b.ch : b.act === 'adax' ? 'adax:' + b.room : b.act === 'gardena' ? 'gardena:' + b.id : b.act === 'zaptec' ? 'zaptec:' + b.id : '';
  if (!key) return [{ error: 'ukjent handling' }, 400];
  await sql('create table if not exists ctl_log (ts timestamptz not null default now(), email text, did text, action text, code int, message text)');
  if ((await sql("select 1 from ctl_log where did = $1 and ts > now() - interval '3 seconds' limit 1", [key])).length) return [{ error: 'for raskt, vent noen sekunder' }, 429];
  let res: Out;
  try { res = b.act === 'shelly' ? await actShelly(b) : b.act === 'adax' ? await actAdax(b) : b.act === 'gardena' ? await actGardena(b) : await actZaptec(b); }
  catch (e: any) { res = [{ error: 'ingen kontakt: ' + String(e?.message || e).slice(0, 60) }, 502]; }
  const [body, st] = res;
  const what = b.act === 'shelly' ? (b.on ? 'på' : 'av') : b.act === 'adax' ? 'sett ' + b.target : b.cmd === 'start' ? 'start ' + b.minutes + ' min' : b.cmd;
  await sql('insert into ctl_log (email, did, action, code, message) values ($1,$2,$3,$4,$5)', [email, key, what, st === 200 ? 0 : st, st === 200 ? null : String(body.error).slice(0, 120)]);
  return res;
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
    if (req.method === 'POST' && req.headers.get('x-push-token')) {
      // Homey rapporterer låsestatus (kun terrassedøren). Eget token, ikke Google-innlogging.
      const tok = (await sql("select value from ctl_secrets where name = 'HOMEY_PUSH_TOKEN'"))[0]?.value;
      if (!tok || req.headers.get('x-push-token') !== tok) return json(req, { error: 'unauthorized' }, 401);
      let b: any; try { b = await req.json(); } catch { return json(req, { error: 'ugyldig' }, 400); }
      if (typeof b.locked !== 'boolean') return json(req, { error: 'ugyldig' }, 400);
      await sql('create table if not exists plug_state (did text primary key, name text, on_state boolean, power double precision, ts timestamptz not null default now())');
      await sql("insert into plug_state (did, name, on_state, power, ts) values ('terrasse','Terrasedør',$1,null,now()) on conflict (did) do update set on_state=$1, ts=now()", [b.locked]);
      return json(req, { ok: true });
    }
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
      if (typeof b.act === 'string') { const [body, st] = await actDispatch(email, b); return json(req, body, st); }
      if (b.lock === 'terrasse') {
        const [body, st] = await lockTerrasse(email, b.unlock === true); return json(req, body, st);
      }
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
