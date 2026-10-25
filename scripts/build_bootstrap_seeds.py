"""Generate platform bootstrap seeds with pinned URLs and SHA-256 receipts."""
import json
from pathlib import Path
import shlex
import zipfile

from build_download_plans import read_json, UV_VERSION


def build(output):
    output = Path(output)
    manifest_path = output / 'release.json'
    manifest = json.loads(manifest_path.read_text())
    uv_release = read_json(f'https://api.github.com/repos/astral-sh/uv/releases/tags/{UV_VERSION}')
    triples = {'linux-x86_64': 'x86_64-unknown-linux-gnu', 'windows-x86_64': 'x86_64-pc-windows-msvc',
               'macos-x86_64': 'x86_64-apple-darwin', 'macos-aarch64': 'aarch64-apple-darwin'}
    manifest['bootstrap_targets'] = {}
    for target, triple in triples.items():
        plan = manifest['download_plans'].get('host', {}).get(target + '/cpu', {})
        if not plan.get('available'):
            continue
        uv_name = f'uv-{triple}.' + ('zip' if target.startswith('windows') else 'tar.gz')
        asset = next(v for v in uv_release['assets'] if v['name'] == uv_name)
        if not (checksum := asset.get('digest', '')).startswith('sha256:'):
            raise ValueError('uv asset lacks SHA-256 metadata')
        uv = {'name': uv_name, 'url': asset['browser_download_url'], 'bytes': asset['size'], 'sha256': checksum[7:], 'kind': 'uv'}
        python = next(v for v in plan['artifacts'] if v['kind'] == 'python')
        libraries = [v for v in plan['artifacts'] if v['kind'] == 'wheel' and v['name'].startswith(('filelock-', 'pyyaml-', 'PyYAML-', 'typing_extensions-'))]
        artifacts = [python, uv, *libraries]
        manifest['bootstrap_targets'][target] = {'artifacts': artifacts}
        downloads = '\n'.join('fetch ' + ' '.join(shlex.quote(str(v[k])) for k in ('url', 'name', 'sha256')) for v in artifacts)
        script = '''#!/bin/sh
set -eu
# Generated from reviewed release artifacts. No dependency resolution occurs here.
root=${NERV_HOME:-"$HOME/.nerv"}
seed="$root/bootstrap/seed"
mkdir -p "$seed" "$root/bin" "$root/python/bootstrap"
check() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1"; else shasum -a 256 "$1"; fi; }
fetch() {
 url=$1; name=$2; expected=$3
 file="$seed/$name"
 if [ -f "$file" ] && [ "$(check "$file" | cut -d ' ' -f 1)" = "$expected" ]; then return; fi
 attempt=0
 while [ "$attempt" -le 5 ]; do
   attempt=$((attempt+1))
   code=0
   curl --fail --location --proto '=https' --proto-redir '=https' --connect-timeout 60 --speed-limit 1 --speed-time 180 --continue-at - --output "$file.partial" "$url" || code=$?
   if [ "$code" = 0 ] && [ "$(check "$file.partial" | cut -d ' ' -f 1)" = "$expected" ]; then mv "$file.partial" "$file"; return; fi
   case "$code" in 0|33|36) mv "$file.partial" "$file.discarded" ;; 23|60|77) return "$code" ;; esac
   [ "$attempt" -le 5 ] && sleep $((attempt*2))
 done
 printf 'Download incomplete. Rerun the installer to continue: %s\\n' "$name" >&2
 return 1
}
'''+downloads+f'''
tar -xzf "$seed/{python['name']}" -C "$root/python/bootstrap"
tar -xzf "$seed/{uv_name}" -C "$seed"
cp "$seed/uv-{triple}/uv" "$root/bin/uv"
chmod +x "$root/bin/uv"
printf '%s\\n' "$root/python/bootstrap/python/bin/python3" > "$root/bootstrap/python-path"
'''
        if not target.startswith('windows'):
            (output / f'seed-{target}.sh').write_text(script)
        else:
            commands = '\n'.join("Fetch '" + "' '".join(str(v[k]).replace("'", "''") for k in ('url', 'name', 'sha256')) + "'" for v in artifacts)
            script = '''$ErrorActionPreference = 'Stop'
$root = $env:NERV_HOME
$seed = Join-Path $root 'bootstrap/seed'
New-Item -ItemType Directory -Force $seed, "$root/bin", "$root/python/bootstrap" | Out-Null
function Fetch($url, $name, $expected) {
 $file = Join-Path $seed $name
 if ((Test-Path $file) -and ((Get-FileHash $file -Algorithm SHA256).Hash.ToLower() -eq $expected)) { return }
 for ($attempt=0; $attempt -le 5; $attempt++) {
  & curl.exe --fail --location --proto '=https' --proto-redir '=https' --connect-timeout 60 --speed-limit 1 --speed-time 180 --continue-at - --output "$file.partial" $url
  $code = $LASTEXITCODE
  if ($code -eq 0 -and (Get-FileHash "$file.partial" -Algorithm SHA256).Hash.ToLower() -eq $expected) { Move-Item -Force "$file.partial" $file; return }
  if ($code -in 0,33,36) { Move-Item -Force "$file.partial" "$file.discarded" }
  if ($code -in 23,60,77) { throw 'Check disk space, access and certificates.' }
  if ($attempt -lt 5) { Start-Sleep -Seconds (($attempt+1)*2) }
 }
 throw "Download incomplete. Rerun the installer to continue: $name"
}
'''+commands+f'''
& tar.exe -xzf "$seed/{python['name']}" -C "$root/python/bootstrap"
if ($LASTEXITCODE -ne 0) {{ throw 'Python extraction failed' }}
Expand-Archive -Force "$seed/{uv_name}" "$seed/uv-unpacked"
$uv = Get-ChildItem "$seed/uv-unpacked" -Filter uv.exe -Recurse | Select-Object -First 1
Copy-Item $uv.FullName "$root/bin/uv.exe"
"$root/python/bootstrap/python/python.exe" | Set-Content "$root/bootstrap/python-path"
'''
            (output / f'seed-{target}.ps1').write_text(script)
    # Include seed scripts in the hash manifest and extracted bundle.
    import hashlib
    for path in output.glob('seed-*.*'):
        manifest['files'][path.name] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'bytes': path.stat().st_size}
    manifest_path.write_text(json.dumps(manifest, indent=2))
    with zipfile.ZipFile(output / f"nerv-{manifest['version']}-bundle.zip", 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(set(manifest['files']) | {'release.json'}):
            archive.write(output / name, name)
