// Leser-test: sjekker signering, token og at de tre pluggene kan leses. Endrer ingenting.
const m = await import('../ctl/dist/index.mjs');
const s = await m.plugStates();
for (const [did, v] of Object.entries(s)) console.log(v.name, '| på:', v.on, '| kode:', v.code);
