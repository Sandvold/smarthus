// HomeyScript: send status for utvalgte enheter til smarthus-portalen (GitHub).
// Kjøres fra en Flow hvert 15. minutt ("Every 15 minutes" -> "Run script").
const TOKEN = 'LIM_INN_GITHUB_TOKEN_HER'; // fine-grained token, kun repoet Sandvold/smarthus

// Navn i Homey -> sted og type
const WANT = {
  'A1':                           { site: 'Arendal', kind: 'mower', label: 'Dreame A1' },
  'A3 AWD Pro 2500':              { site: 'Oslo',    kind: 'mower', label: 'Dreame A3 AWD Pro' },
  'Guldberglia 3: Terrasedør':    { site: 'Oslo',    kind: 'lock',  label: 'Terrasedør' },
  'Revesandveien 156: Inngangsdør': { site: 'Arendal', kind: 'lock', label: 'Inngangsdør' },
  'Guldberglia 3':                { site: 'Oslo',    kind: 'daikin', label: 'Daikin' },
};

const devices = await Homey.devices.getDevices();
const out = [];
for (const d of Object.values(devices)) {
  const m = WANT[d.name];
  if (!m) continue;
  const data = {};
  for (const [k, c] of Object.entries(d.capabilitiesObj || {})) {
    if (c && (typeof c.value === 'number' || typeof c.value === 'string' || typeof c.value === 'boolean')) data[k] = c.value;
  }
  if (m.kind === 'daikin' && !('target_temperature' in data) && !('measure_temperature' in data)) continue; // hopp over andre enheter med samme navn
  out.push({ id: d.id, name: m.label, site: m.site, kind: m.kind, available: d.available !== false, driver: d.driverId, data });
}

const r = await fetch('https://api.github.com/repos/Sandvold/smarthus/dispatches', {
  method: 'POST',
  headers: { Authorization: 'Bearer ' + TOKEN, Accept: 'application/vnd.github+json', 'Content-Type': 'application/json' },
  body: JSON.stringify({ event_type: 'homey', client_payload: { devices: out } }),
});
log('Sendte ' + out.length + ' enheter, GitHub svarte ' + r.status);
