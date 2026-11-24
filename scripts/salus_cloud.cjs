'use strict';
/* Salus Premium Lite via skyplattformen (Cognito SRP + REST + AWS IoT MQTT).
   Protokollen er reverse-engineeret av Peterka35/salus-it600-cloud og brukt i mrcobra/Hubitat-Salus-UGE600-Cloud-integration.
   MODE=read  (standard): leser alle termostater, skriver site/salus2.json og site/salustest.json. Kun lesing.
   MODE=write: setter ÉN termostat (WRITE_DEVICE = navn eller kode, WRITE_SETPOINT i °C, 5-28) og bekrefter via skyggen.
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

const STEPS = ['Logge inn (Cognito)', 'Finne gateway og enheter', 'Lese temperatur og settpunkt', 'Hente AWS-nøkler', 'Koble til meldingskø (MQTT)', 'Skrive til én termostat', 'Bekrefte at termostaten svarte'];
const OUT = { updated: new Date().toISOString(), source: 'sky', steps: STEPS.map((n, i) => ({ n: i + 1, name: n, status: 'venter', detail: '' })), devices: [], api: {}, write: { status: 'venter', detail: 'Ikke kjørt. Venter på godkjenning før noe skrives til termostatene.' } };
const clean = s => { s = String(s); for (const v of [E, P]) if (v) s = s.split(v).join('<skjult>'); return s.slice(0, 300); };
const rec = (i, status, detail = '') => { OUT.steps[i].status = status; OUT.steps[i].detail = clean(detail); };
const save = () => { fs.mkdirSync('site', { recursive: true }); fs.writeFileSync('site/salustest.json', JSON.stringify(OUT)); };
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
  let rows = [], all = null;
  try {
    await login(); rec(0, 'ok', 'Innlogget via Cognito SRP');
    all = await readAll();
    rows = all.devs.map(toRow);
    const th = rows.filter(r => r.temp != null);
    rec(1, rows.length ? 'ok' : 'feil', `${all.gws.length} gateway, ${rows.length} enheter, ${th.length} med temperatur`);
    rec(2, th.length ? 'ok' : 'feil', `Leste ${th.length} termostater: ` + th.map(r => `${r.name} ${r.temp}° (innstilt ${r.setpoint ?? '–'}°)`).join(', '));
    OUT.devices = rows;
    save();
    if (MODE !== 'write') {
      // salus2.json brukes av siden; bare termostater med temperatur
      fs.writeFileSync('site/salus2.json', JSON.stringify({ updated: new Date().toISOString(), source: 'sky', devices: th }));
      rec(3, 'venter', 'Ikke nødvendig ved lesing'); rec(4, 'venter', 'Ikke nødvendig ved lesing'); rec(5, 'venter', 'Venter på godkjenning fra deg'); rec(6, 'venter', '');
      save(); console.log('Lesing ferdig:', th.length, 'termostater'); return;
    }
    // ---- Skriving: én termostat ----
    const want = (process.env.WRITE_DEVICE || '').trim(), val = Number(String(process.env.WRITE_SETPOINT || '').replace(',', '.'));
    const tgt = th.filter(r => r.code === want || r.name.toLowerCase() === want.toLowerCase());
    if (tgt.length !== 1) throw new Error(`Fant ${tgt.length} termostater som heter «${want}». Navn: ${th.map(r => r.name).join(', ')}`);
    if (!(val >= MIN_T && val <= MAX_T)) throw new Error(`Settpunktet ${val} er utenfor grensen ${MIN_T}-${MAX_T} °C`);
    const t = tgt[0], idx = t.index || '11', before = t.setpoint;
    const cr = await awsCreds(); rec(3, 'ok', 'Midlertidige AWS-nøkler hentet');
    const gwCode = (all.devs.find(d => d.device_code === t.code) || {})._gw;
    const cli = await mqttConnect(cr, gwCode); rec(4, 'ok', 'Tilkoblet AWS IoT');
    const payload = JSON.stringify({ state: { desired: { [idx]: { properties: { [K + 'SetHeatingSetpoint_x100']: Math.round(val * 100), [K + 'SetHoldType']: 2 } } } } });
    await publish(cli, `$aws/things/${t.code}/shadow/update`, payload);
    cli.end(true);
    rec(5, 'ok', `Sendte «${t.name}» fra ${before ?? '?'} til ${val} °C (manuell hold)`);
    OUT.write = { status: 'sendt', detail: `Sendte ${t.name}: ${before ?? '?'} -> ${val} °C. Venter på bekreftelse.`, device: t.name, value: val, before };
    save();
    let ok = false, now = null;
    for (let i = 0; i < 8 && !ok; i++) {
      await sleep(5000);
      const r = await api('POST', '/devices/device_shadows', { request_id: 'smarthus', device_codes: [t.code] });
      const it = ((r.data || {}).success_list || [])[0];
      if (it) { const rep = (JSON.parse(it.payload).state || {}).reported || {}; for (const v of Object.values(rep)) if (v && v.properties) { now = x100(v.properties[K + 'HeatingSetpoint_x100']); break; } }
      ok = now != null && Math.abs(now - val) < 0.01;
    }
    rec(6, ok ? 'ok' : 'feil', ok ? `Termostaten melder nå ${now} °C` : `Termostaten meldte fortsatt ${now ?? 'ingenting'} °C etter 40 s (kommandoen kan være i kø)`);
    OUT.write = { status: ok ? 'ok' : 'ukjent', detail: ok ? `Bekreftet: ${t.name} melder ${now} °C (var ${before ?? '?'})` : `Sendt, men ikke bekreftet innen 40 s. Siste verdi ${now ?? '–'} °C`, device: t.name, value: val, before, after: now };
    save(); console.log(OUT.write.detail);
  } catch (e) {
    const i = OUT.steps.findIndex(s => s.status === 'venter');
    rec(i < 0 ? 0 : i, 'feil', 'Stoppet: ' + (e && e.message || e));
    if (MODE === 'write') OUT.write = { status: 'feil', detail: clean(e && e.message || e) };
    save(); console.log('Feil:', clean(e && e.message || e));
    process.exitCode = 0;
  }
})().then(() => process.exit(0));
