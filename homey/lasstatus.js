// HomeyScript: rapporterer låsestatus for terrassedøren til smarthus-portalen.
// Kjøres fra Flow-en laas_terrasse: Lock -> Delay 5 sek -> Run script.
const TOKEN = 'LIM_INN_PUSH_TOKEN_HER';
const URL = 'https://br-shy-hat-ba72i2iy-ctl.compute.c-8.eu-central-1.aws.neon.tech/';

const devices = await Homey.devices.getDevices();
const d = Object.values(devices).find(x => x.name === 'Guldberglia 3: Terrasedør');
if (!d) throw new Error('Fant ikke Terrasedør');
const locked = d.capabilitiesObj && d.capabilitiesObj.locked ? d.capabilitiesObj.locked.value : null;
if (typeof locked !== 'boolean') throw new Error('Ingen låsestatus');
const r = await fetch(URL, {
  method: 'POST',
  headers: { 'x-push-token': TOKEN, 'Content-Type': 'application/json' },
  body: JSON.stringify({ locked }),
});
log('Låst: ' + locked + ', portalen svarte ' + r.status);
