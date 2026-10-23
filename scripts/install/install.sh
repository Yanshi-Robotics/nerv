#!/bin/sh
set -eu
NERV_HOME=${NERV_HOME:-"$HOME/.nerv"}
export NERV_HOME
release_base=${NERV_RELEASE_BASE:-https://github.com/Yanshi-Robotics/nerv/releases/latest/download}
bundle=""
modify_path=1
while [ "$#" -gt 0 ]; do
  case "$1" in
    --bundle) bundle=${2:?Provide the extracted release directory}; shift 2 ;;
    --no-path) modify_path=0; shift ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
done
mkdir -p "$NERV_HOME/bin" "$NERV_HOME/bootstrap"
if command -v nerv >/dev/null 2>&1; then printf 'Existing nerv command: %s\n' "$(command -v nerv)"; fi
# Pin the bootstrap tool; no training environment or system package is changed.
if [ ! -x "$NERV_HOME/bin/uv" ]; then
  curl -LsSf https://astral.sh/uv/0.12.2/install.sh | UV_UNMANAGED_INSTALL="$NERV_HOME/bin" sh
fi
if [ -n "$bundle" ]; then
  cp "$bundle/bootstrap.py" "$NERV_HOME/bootstrap/install.py"
  cp "$bundle/launch.py" "$NERV_HOME/bootstrap/launch.py"
  manifest="$bundle/release.json"
else
  curl -fL "$release_base/bootstrap.py" -o "$NERV_HOME/bootstrap/install.py"
  curl -fL "$release_base/release.json" -o "$NERV_HOME/bootstrap/release.json"
  curl -fL "$release_base/launch.py" -o "$NERV_HOME/bootstrap/launch.py"
  manifest="$NERV_HOME/bootstrap/release.json"
fi
UV_PYTHON_INSTALL_DIR="$NERV_HOME/python" UV_CREDENTIALS_DIR="$NERV_HOME/.cache/uv-credentials" UV_CACHE_DIR="$NERV_HOME/.cache/uv" "$NERV_HOME/bin/uv" run --managed-python --no-project --python 3.12 "$NERV_HOME/bootstrap/install.py" --manifest "$manifest"
cat > "$NERV_HOME/bin/nerv" <<'EOF'
#!/bin/sh
NERV_HOME=${NERV_HOME:-"$HOME/.nerv"}
export NERV_HOME
export UV_PYTHON_INSTALL_DIR="$NERV_HOME/python"
export UV_CACHE_DIR="$NERV_HOME/.cache/uv"
export UV_CREDENTIALS_DIR="$NERV_HOME/.cache/uv-credentials"
exec "$NERV_HOME/bin/uv" run --offline --managed-python --no-project --python 3.12 "$NERV_HOME/bootstrap/launch.py" "$@"
EOF
chmod +x "$NERV_HOME/bin/nerv"
case "${SHELL:-}" in
  */zsh) profile="$HOME/.zshrc" ;;
  */bash) profile="$HOME/.bashrc" ;;
  *) profile="$HOME/.profile" ;;
esac
# Append one path entry; do not replace existing profile content.
if [ "$modify_path" = 1 ] && { ! test -f "$profile" || ! grep -Fq '# NERV launcher' "$profile"; }; then
  printf '\n%s\n%s\n' '# NERV launcher' 'export PATH="${NERV_HOME:-$HOME/.nerv}/bin:$PATH"' >> "$profile"
fi
printf '\nInstalled. Open a new terminal and run nerv. Start now with: %s/bin/nerv\n' "$NERV_HOME"
