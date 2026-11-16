# smarthus

Henter målinger fra Tuya (Smart Life) hvert 15. minutt, lagrer dem i Neon og publiserer et dashbord på GitHub Pages.

Secrets (Settings → Secrets and variables → Actions): `TUYA_ACCESS_ID`, `TUYA_ACCESS_SECRET`, `NEON_DATABASE_URL`.
Variabel: `TUYA_HOST` (bare hvis datasenteret ikke er Central Europe).
Enheter legges i `config/devices.json`.
