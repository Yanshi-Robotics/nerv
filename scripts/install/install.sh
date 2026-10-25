#!/bin/sh
set -eu
NERV_HOME=${NERV_HOME:-"$HOME/.nerv"}
export NERV_HOME
release_base=${NERV_RELEASE_BASE:-https://github.com/Yanshi-Robotics/nerv/releases/latest/download}
bundle=""; modify_path=1; target=""; compute=auto
while [ "$#" -gt 0 ]; do
  case "$1" in
    --bundle) bundle=${2:?Provide the extracted release directory}; shift 2 ;;
    --target) target=${2:?Provide the target platform}; shift 2 ;;
    --compute) compute=${2:?Choose auto, cpu or cu130}; shift 2 ;;
    --no-path) modify_path=0; shift ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
done
case "$compute" in auto|cpu|cu130) ;; *) echo 'Unsupported compute selection' >&2; exit 2 ;; esac
case "$(uname -s)/$(uname -m)" in
 Linux/x86_64) detected=linux-x86_64 ;;
 Darwin/arm64) detected=macos-aarch64 ;;
 Darwin/x86_64) detected=macos-x86_64 ;;
 *) detected="" ;;
esac
if [ -z "$target" ]; then target=$detected; fi
case "$target" in linux-x86_64|macos-aarch64|macos-x86_64) ;; *) echo 'Cannot identify a supported platform. Supply --target or use a supported host.' >&2; exit 2 ;; esac
if [ -n "$detected" ] && [ "$target" != "$detected" ]; then echo 'Target conflicts with this process architecture' >&2; exit 2; fi
if [ "$(uname -s)" = Darwin ] && [ "$(sw_vers -productVersion | cut -d . -f 1)" -lt 14 ]; then echo 'This release requires macOS 14 or newer.' >&2; exit 2; fi
command -v curl >/dev/null || { echo 'Install curl with HTTPS support first.' >&2; exit 2; }
mkdir -p "$NERV_HOME/bin" "$NERV_HOME/bootstrap"
if command -v nerv >/dev/null 2>&1; then printf 'Existing nerv command: %s\n' "$(command -v nerv)"; fi
# These small entry files establish the release manifest. Large files use the
# generated, checksum-pinned seed, then the persistent NERV download manager.
get_entry() {
 if [ -n "$bundle" ]; then cp "$bundle/$1" "$NERV_HOME/bootstrap/$2";
 else curl --fail --location --proto '=https' --proto-redir '=https' --retry 5 --connect-timeout 60 --max-time 180 "$release_base/$1" -o "$NERV_HOME/bootstrap/$2"; fi
}
get_entry "seed-$target.sh" seed.sh
sh "$NERV_HOME/bootstrap/seed.sh"
get_entry bootstrap.py install.py
get_entry launch.py launch.py
get_entry release.json release.json
python=$(cat "$NERV_HOME/bootstrap/python-path")
"$python" - "$NERV_HOME/bootstrap" <<'PY'
import hashlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1]); manifest = json.loads((root / 'release.json').read_text())
for published, local in [('bootstrap.py', 'install.py'), ('launch.py', 'launch.py')]:
    if hashlib.sha256((root / local).read_bytes()).hexdigest() != manifest['files'][published]['sha256']:
        raise SystemExit('Installer checksum mismatch: ' + published)
PY
manifest="$NERV_HOME/bootstrap/release.json"
if [ -n "$bundle" ]; then manifest="$bundle/release.json"; fi
"$python" "$NERV_HOME/bootstrap/install.py" --manifest "$manifest" --target "$target" --compute "$compute"
cat > "$NERV_HOME/bin/nerv" <<'LAUNCH'
#!/bin/sh
NERV_HOME=${NERV_HOME:-"$HOME/.nerv"}
export NERV_HOME
python=$(cat "$NERV_HOME/bootstrap/python-path")
exec "$python" "$NERV_HOME/bootstrap/launch.py" "$@"
LAUNCH
chmod +x "$NERV_HOME/bin/nerv"
case "${SHELL:-}" in
  */zsh) profile="$HOME/.zshrc" ;;
  */bash) profile="$HOME/.bashrc" ;;
  *) profile="$HOME/.profile" ;;
esac
if [ "$modify_path" = 1 ] && { ! test -f "$profile" || ! grep -Fq '# NERV launcher' "$profile"; }; then
  printf '\n%s\n%s\n' '# NERV launcher' 'export PATH="${NERV_HOME:-$HOME/.nerv}/bin:$PATH"' >> "$profile"
fi
printf '\nInstalled. Open a new terminal and run nerv. Start now with: %s/bin/nerv\n' "$NERV_HOME"
