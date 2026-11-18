# smarthus

Privat smarthus-dashbord, bare for intern bruk.

- **Innhenting:** GitHub Actions (`.github/workflows/collect.yml`, cron hver 3. time) henter målinger fra Tuya/Smart Life, Tibber, Zaptec, Salus, LG, Miele, Aqara, Shelly, Adax, Gardena, Microtemp m.fl. og lagrer dem i Neon. Homey sender hendelser via `repository_dispatch` (type `homey`) til `homey.yml`, som bare lagrer Homey-data og publiserer `homey.json`.
- **Visning:** `site/index.html` publiseres på GitHub Pages. Selve siden er åpen, men data ligger i Neon bak innlogging (Google via Neon Auth).
- **Styring:** `ctl/index.ts` er en Neon-funksjon som krever gyldig innlogging og e-post på tillatelseslisten.

Secrets (Settings → Secrets and variables → Actions): `NEON_DATABASE_URL` og leverandørnøklene hvert script leser (se `env:` i workflowene).
Variabel: `TUYA_HOST` (bare hvis datasenteret ikke er Central Europe).
Enheter legges i `config/`.
