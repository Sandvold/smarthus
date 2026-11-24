'use strict';
/* Salus Premium Lite via skyplattformen (Cognito SRP + REST + AWS IoT MQTT).
   Protokollen er reverse-engineeret av Peterka35/salus-it600-cloud og brukt i mrcobra/Hubitat-Salus-UGE600-Cloud-integration.
   MODE=read  (standard): leser alle termostater og skriver site/salus2.json. Kun lesing; rører ikke salustest.json.
   MODE=write: setter én termostat (WRITE_DEVICE = navn, kode eller «alle», WRITE_SETPOINT i °C, 5-28) og bekrefter via skyggen.
   MODE=queue: utfører ventende rader i tabellen salus_queue (lagt inn av ctl ved «steng/åpne hytta»), siste verdi per termostat vinner.
   Passord og tokens skrives aldri ut. */
const fs = require('fs');
const { randomUUID } = require('crypto');
global.fetch = require('node-fetch');
try { Object.defineProperty(global, 'navigator', { value: { userAgent: 'node' }, writable: true, configurable: true }); } catch (e) {}
const fetch = require('node-fetch');
const ACI = require('amazon-cognito-identity-js');

const E = (process.env.SALUS_EMAIL || '').trim(), P = (process.env.SALUS_PASSWORD || '').trim();
const MODE = (process.env.MODE || 'read').toLowerCase();
const REGION = 'eu-central-1', POOL = 'eu-central-1_XGRz3CgoY', CLIENT = '4pk5efh3v84g5dav43imsv4fbj';
const IDPOOL = 'eu-central-1:60912c00-287d-413b-a2c9-ece3ccef9230', IOT = 'a24u3z7zzwrtdl-ats.iot.eu-central-1.amazonaws.com';
const API = 'https://service-api.eu.premium.salusconnect.io/api/v1', COMPANY = 'salus-eu';
const MIN_T = 5, MAX_T = 28;
const K = 'ep9:sIT600TH:';

const STEPS = ['Logge inn (Cognito)', 'Finne gateway og enheter', 'Lese temperatur og settpunkt', 'Hente AWS-nøkler', 'Koble til meldingskø (MQTT)', 'Skrive til termostatene', 'Bekrefte at termostaten svarte'];
const OUT = { updated: new Date().toISOString(), source: 'sky', steps: STEPS.map((n, i) => ({ n: i + 1, name: n, status: 'venter', detail: '' })), devices: [], api: {}, write: { status: 'venter', detail: 'Ikke kjørt. Venter på godkjenning før noe skrives til termostatene.' } };
const clean = s => { s = String(s); for (const v of [E, P]) if (v) s = s.split(v).join('<skjult>'); return s.slice(0, 300); };
const rec = (i, status, detail = '') => { OUT.steps[i].status = status; OUT.steps[i].detail = clean(detail); };
// salustest.json beskriver siste SKRIVING. Lesing (hvert kvarter) og tom kø skal ikke overskrive den; salus2.json har lesingen.
const save = () => { if (MODE === 'read' || OUT.write?.status === 'tom') return; fs.mkdirSync('site', { recursive: true }); fs.writeFileSync('site/salustest.json', JSON.stringify(OUT)); };
async function sqlq(query, params = []) {
  const url = process.env.NEON_DATABASE_URL; if (!url) throw new Error('NEON_DATABASE_URL mangler');
  const r = await fetch(`https://${new URL(url).host}/sql`, { method: 'POST', headers: { 'Neon-Connection-String': url, 'content-type': 'application/json' }, body: JSON.stringify({ query, params }) });
  if (!r.ok) throw new Error('Neon SQL ga HTTP ' + r.status);
  return (await r.json()).rows || [];
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

let tokens = null;
function login() {
  return new Promise((resolve, reject) => {
    const pool = new ACI.CognitoUserPool({ UserPoolId: POOL, ClientId: CLIENT });
    const user = new ACI.CognitoUser({ Username: E, Pool: pool });
    user.setAuthenticationFlowType('USER_SRP_AUTH');
    user.authenticateUser(new ACI.AuthenticationDetails({ Username: E, Password: P }), {
      onSuccess: s => { tokens = { access: s.getAccessToken().getJwtToken(), id: s.getIdToken().getJwtToken() }; resolve(); },
      onFailure: e => reject(e),
    });
  });
}
async function api(method, ep, body) {
  const r = await fetch(API + ep, { method, headers: { 'Content-Type': 'application/json', 'x-access-token': tokens.access, 'x-auth-token': tokens.id, 'x-company-code': COMPANY }, body: body ? JSON.stringify(body) : undefined });
  if (!r.ok) throw new Error(`Salus API ${method} ${ep.split('?')[0]} ga HTTP ${r.status}`);
  return r.json();
}
async function readAll() {
  const sl = await api('GET', '/occupants/slider_list');
  const gws = ((sl && sl.data) || []).filter(i => i.type === 'gateway');
  const devs = [];
  for (const gw of gws) {
    const det = ((await api('GET', `/occupants/slider_details?id=${gw.id}&type=gateway`)) || {}).data || {};
    for (const it of det.items || []) {
      if (it.rule_trigger_key || (it.dashboard_attributes || {}).type === 'one_touch_rule') continue;
      it._gw = (gw.gateway || {}).device_code; it._gwname = gw.name || (gw.gateway || {}).name;
      devs.push(it);
    }
  }
  const codes = devs.map(d => d.device_code).filter(Boolean);
  const sh = {};
  if (codes.length) {
    const r = await api('POST', '/devices/device_shadows', { request_id: 'smarthus', device_codes: codes });
    for (const it of ((r && r.data && r.data.success_list) || [])) { try { sh[it.device_code] = JSON.parse(it.payload); } catch (e) {} }
  }
  for (const d of devs) {
    const rep = ((sh[d.device_code] || {}).state || {}).reported || {};
    for (const [idx, val] of Object.entries(rep)) if (val && typeof val === 'object' && val.properties) { d._idx = idx; d._p = val.properties; d._model = val.model; break; }
  }
  return { gws, devs };
}
const x100 = v => (v == null || isNaN(Number(v))) ? null : Number(v) / 100;
const toRow = d => {
  const p = d._p || {};
  return { code: d.device_code, name: d.name || (d.dashboard_attributes || {}).name || 'Enhet', model: d._model || (d.dashboard_attributes || {}).type || '', gateway: d._gwname || null, index: d._idx || null,
    temp: x100(p[K + 'LocalTemperature_x100']), setpoint: x100(p[K + 'HeatingSetpoint_x100']), hum: p[K + 'SunnySetpoint_x100'] ?? null, battery: p[K + 'BatteryLevel'] ?? null,
    running: p[K + 'RunningState'] ?? null, hold: p[K + 'HoldType'] ?? null, mode: p[K + 'SystemMode'] ?? null, connected: d._p != null };
};

async function awsCreds() {
  const logins = { [`cognito-idp.${REGION}.amazonaws.com/${POOL}`]: tokens.id };
  const post = async (target, body) => { const r = await fetch(`https://cognito-identity.${REGION}.amazonaws.com/`, { method: 'POST', headers: { 'Content-Type': 'application/x-amz-json-1.1', 'X-Amz-Target': target }, body: JSON.stringify(body) }); if (!r.ok) throw new Error(`AWS ${target.split('.')[1]} ga HTTP ${r.status}`); return r.json(); };
  const id = await post('AWSCognitoIdentityService.GetId', { IdentityPoolId: IDPOOL, Logins: logins });
  const c = await post('AWSCognitoIdentityService.GetCredentialsForIdentity', { IdentityId: id.IdentityId, Logins: logins });
  return { accessKeyId: c.Credentials.AccessKeyId, secretKey: c.Credentials.SecretKey, sessionToken: c.Credentials.SessionToken };
}
function mqttConnect(cr, gwCode) {
  const awsIot = require('aws-iot-device-sdk');
  const c = awsIot.device({ region: REGION, host: IOT, clientId: `${gwCode || 'smarthus'}-${randomUUID()}`, protocol: 'wss', accessKeyId: cr.accessKeyId, secretKey: cr.secretKey, sessionToken: cr.sessionToken, keepalive: 60, reconnectPeriod: 0 });
  return new Promise((res, rej) => { const t = setTimeout(() => rej(new Error('MQTT-tilkobling tok for lang tid')), 20000); c.on('connect', () => { clearTimeout(t); res(c); }); c.on('error', e => { clearTimeout(t); rej(e); }); });
}
const publish = (c, topic, payload) => new Promise((res, rej) => c.publish(topic, payload, { qos: 1 }, e => e ? rej(e) : res()));

(async () => {
  if (!(E && P)) { rec(0, 'feil', 'SALUS_EMAIL/SALUS_PASSWORD mangler i GitHub-secrets'); save(); return; }
  let rows = [], all = null, qids = [];
  try {
    await login(); rec(0, 'ok', 'Innlogget via Cognito SRP');
    all = await readAll();
    rows = all.devs.map(toRow);
    const th = rows.filter(r => r.temp != null);
    rec(1, rows.length ? 'ok' : 'feil', `${all.gws.length} gateway, ${rows.length} enheter, ${th.length} med temperatur`);
    rec(2, th.length ? 'ok' : 'feil', `Leste ${th.length} termostater: ` + th.map(r => `${r.name} ${r.temp}° (innstilt ${r.setpoint ?? '–'}°)`).join(', '));
    OUT.devices = rows;
    save();
    if (MODE === 'read') {
      // salus2.json brukes av siden; bare termostater med temperatur
      fs.writeFileSync('site/salus2.json', JSON.stringify({ updated: new Date().toISOString(), source: 'sky', devices: th }));
      rec(3, 'venter', 'Ikke nødvendig ved lesing'); rec(4, 'venter', 'Ikke nødvendig ved lesing'); rec(5, 'venter', 'Venter på godkjenning fra deg'); rec(6, 'venter', '');
      save(); console.log('Lesing ferdig:', th.length, 'termostater'); return;
    }
    // ---- Skriving (MODE=write: WRITE_DEVICE navn eller «alle»; MODE=queue: rader i salus_queue) ----
    const plan = new Map();   // kode -> {r, val}
    const addPlan = (dev, val) => {
      if (!(val >= MIN_T && val <= MAX_T)) throw new Error(`Settpunktet ${val} er utenfor grensen ${MIN_T}-${MAX_T} °C`);
      const hit = /^(alle|\*)$/i.test(dev) ? th : th.filter(r => r.code === dev || r.name.toLowerCase() === dev.toLowerCase());
      if (!hit.length) throw new Error(`Fant ingen termostat som heter «${dev}». Navn: ${th.map(r => r.name).join(', ')}`);
      for (const r of hit) plan.set(r.code, { r, val });
    };
    if (MODE === 'queue') {
      await sqlq('create table if not exists salus_queue (id bigserial primary key, created_at timestamptz not null default now(), device text not null, setpoint real not null, status text not null default \'ny\', result text, done_at timestamptz)');
      await sqlq("update salus_queue set status = 'utløpt', done_at = now() where status = 'ny' and created_at < now() - interval '3 hours'");
      await sqlq("update salus_queue set status = 'feil', result = 'Kjøringen stoppet uten svar', done_at = now() where status = 'kjører' and created_at < now() - interval '30 minutes'");
      const q = await sqlq("select id, device, setpoint from salus_queue where status = 'ny' order by id");
      if (!q.length) { OUT.write = { status: 'tom', detail: 'Ingen ventende Salus-kommandoer i køen' }; rec(5, 'venter', 'Ingen ventende kommandoer'); save(); console.log('Køen er tom'); return; }
      qids = q.map(x => x.id);
      await sqlq('update salus_queue set status = \'kjører\' where id = any($1::bigint[])', [qids]);
      try { for (const x of q) addPlan(String(x.device), Number(x.setpoint)); } catch (e) { await sqlq("update salus_queue set status = 'feil', result = $2, done_at = now() where id = any($1::bigint[])", [qids, clean(e.message)]); throw e; }
    } else {
      addPlan((process.env.WRITE_DEVICE || '').trim(), Number(String(process.env.WRITE_SETPOINT || '').replace(',', '.')));
    }
    const targets = [...plan.values()];
    const cr = await awsCreds(); rec(3, 'ok', 'Midlertidige AWS-nøkler hentet');
    const gwCode = (all.devs.find(d => d.device_code === targets[0].r.code) || {})._gw;
    const cli = await mqttConnect(cr, gwCode); rec(4, 'ok', 'Tilkoblet AWS IoT');
    for (const { r, val } of targets) {
      const payload = JSON.stringify({ state: { desired: { [r.index || '11']: { properties: { [K + 'SetHeatingSetpoint_x100']: Math.round(val * 100), [K + 'SetHoldType']: 2 } } } } });
      await publish(cli, `$aws/things/${r.code}/shadow/update`, payload);
      await sleep(700);
    }
    cli.end(true);
    rec(5, 'ok', 'Sendte: ' + targets.map(t => `${t.r.name} ${t.r.setpoint ?? '?'}→${t.val} °C`).join(', '));
    OUT.write = { status: 'sendt', detail: 'Sendt til ' + targets.length + ' termostater. Venter på bekreftelse.' };
    save();
    const left = new Map(targets.map(t => [t.r.code, t])), now = {};
    for (let i = 0; i < 8 && left.size; i++) {
      await sleep(5000);
      const r = await api('POST', '/devices/device_shadows', { request_id: 'smarthus', device_codes: [...left.keys()] });
      for (const it of ((r.data || {}).success_list || [])) {
        let rep = {}; try { rep = (JSON.parse(it.payload).state || {}).reported || {}; } catch (e) {}
        for (const v of Object.values(rep)) if (v && v.properties) { now[it.device_code] = x100(v.properties[K + 'HeatingSetpoint_x100']); break; }
        const t = left.get(it.device_code);
        if (t && now[it.device_code] != null && Math.abs(now[it.device_code] - t.val) < 0.01) left.delete(it.device_code);
      }
    }
    const okN = targets.filter(t => !left.has(t.r.code)).map(t => t.r.name), failN = [...left.values()].map(t => `${t.r.name} (melder ${now[t.r.code] ?? '–'})`);
    rec(6, failN.length ? 'feil' : 'ok', failN.length ? `Ikke bekreftet: ${failN.join(', ')}` : `Alle ${okN.length} termostater melder ny verdi`);
    const det = (failN.length ? `Bekreftet ${okN.length} av ${targets.length}. Ikke bekreftet: ${failN.join(', ')}` : `Bekreftet: ${okN.join(', ')} melder ${targets[0].val} °C`);
    OUT.write = { status: failN.length ? 'ukjent' : 'ok', detail: det, devices: okN, value: targets[0].val };
    if (MODE === 'queue' && qids.length) await sqlq("update salus_queue set status = $2, result = $3, done_at = now() where id = any($1::bigint[])", [qids, failN.length ? 'feil' : 'ok', det.slice(0, 200)]);
    // oppdater salus2.json så siden viser nye verdier
    try { const again = (await readAll()).devs.map(toRow).filter(r => r.temp != null); fs.writeFileSync('site/salus2.json', JSON.stringify({ updated: new Date().toISOString(), source: 'sky', devices: again })); } catch (e) {}
    save(); console.log(det);
  } catch (e) {
    const i = OUT.steps.findIndex(s => s.status === 'venter');
    rec(i < 0 ? 0 : i, 'feil', 'Stoppet: ' + (e && e.message || e));
    if (MODE === 'write' || MODE === 'queue') OUT.write = { status: 'feil', detail: clean(e && e.message || e) };
    if (MODE === 'queue' && qids.length) { try { await sqlq("update salus_queue set status = 'feil', result = $2, done_at = now() where id = any($1::bigint[]) and status = 'kjører'", [qids, clean(e && e.message || e)]); } catch (e2) {} }
    save(); console.log('Feil:', clean(e && e.message || e));
    process.exitCode = 0;
  }
})().then(() => process.exit(0));
