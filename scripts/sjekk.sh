#!/usr/bin/env bash
# Rask sjekk før push: syntaks i siden, Python-scripts, workflow-filer og styringsfunksjonen.
set -u
cd "$(dirname "$0")/.."
fail=0
ok(){ echo "  ok   $1"; }
bad(){ echo "  FEIL $1"; fail=1; }

echo "Siden (site/index.html)"
if node -e "const s=require('fs').readFileSync('site/index.html','utf8');const m=s.match(/<script>([\s\S]*)<\/script>/);if(!m)throw new Error('ingen script-blokk');new Function(m[1].replace(/import\(/g,'(0,()=>0)('))" 2>/tmp/sjekk_err; then ok "JavaScript-syntaks"; else bad "JavaScript: $(head -c 300 /tmp/sjekk_err)"; fi

echo "Python-scripts"
for f in scripts/*.py; do python3 -m py_compile "$f" 2>/tmp/sjekk_err && ok "$f" || bad "$f: $(head -c 200 /tmp/sjekk_err)"; done
find scripts -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null

echo "Workflow-filer"
for f in .github/workflows/*.yml; do python3 -c "import yaml,sys;yaml.safe_load(open('$f'))" 2>/tmp/sjekk_err && ok "$f" || bad "$f: $(head -c 200 /tmp/sjekk_err)"; done

echo "Styringsfunksjonen (ctl/index.ts)"
if [ -d ctl/node_modules/esbuild ] || command -v esbuild >/dev/null 2>&1; then
  (cd ctl && npx esbuild index.ts --bundle --platform=node --target=node24 --format=esm --outfile=/tmp/ctl_check.mjs >/dev/null 2>/tmp/sjekk_err) && ok "bygger" || bad "bygg: $(head -c 300 /tmp/sjekk_err)"
else echo "  hopper over (esbuild ikke installert: cd ctl && npm i esbuild)"; fi

[ $fail -eq 0 ] && echo "Alt OK" || echo "Det er feil over"
exit $fail
