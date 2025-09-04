#!/bin/bash
# Enhanced setup script for LeoScribeBot (whisper.cpp + 3-layer text correction)

set -euo pipefail

# ---------- Colors ----------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_status()  { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
print_error()   { echo -e "${RED}[ERROR]${NC} $1"; }

# ---------- Sanity checks ----------
if ! command -v apt >/dev/null 2>&1; then
  print_error "This script targets Debian/Ubuntu (apt). For other distros, install equivalent packages manually."
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  print_status "Installing curl..."
  sudo apt update && sudo apt install -y curl
fi

# ---------- (Optional) Caddy GPG key fix ----------
print_status "Checking/adding Caddy GPG key…"
curl -fsSL 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable.gpg || true
print_success "Caddy GPG key step complete."

# ---------- System updates ----------
print_status "Updating system packages…"
sudo apt update
sudo apt -y upgrade

# ---------- System dependencies ----------
print_status "Installing base system dependencies…"
sudo apt install -y \
  python3 python3-pip python3-venv python3-dev \
  build-essential git pkg-config \
  portaudio19-dev \
  ffmpeg \
  libffi-dev \
  libsodium-dev \
  libopus0

# (Compatibility fallback: some environments use libnacl-dev; harmless if already satisfied)
sudo apt install -y libnacl-dev || true

print_success "System dependencies installed."

# ---------- Python version ----------
print_status "Checking Python version…"
PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
if dpkg --compare-versions "$PYTHON_VERSION" "lt" "3.8"; then
  print_error "Python 3.8+ required. Detected $PYTHON_VERSION."
  exit 1
else
  print_success "Python $PYTHON_VERSION is compatible."
fi

# ---------- Virtual environment (clean rebuild) ----------
print_status "Creating virtual environment…"
if [ -d "venv" ]; then
  print_status "Removing existing venv for a clean install…"
  rm -rf venv
fi
python3 -m venv venv
source venv/bin/activate
print_success "Virtual environment ready."

# ---------- Python deps ----------
print_status "Upgrading pip, wheel, setuptools…"
pip install --upgrade pip wheel setuptools

print_status "Installing Python dependencies from requirements.txt…"
pip install -r requirements.txt

# If whispercpp wasn't included in requirements, try to install it (fallback to whisper-cpp-python)
python - <<'PY'
import importlib, sys, subprocess
def has(mod):
    try:
        importlib.import_module(mod)
        return True
    except Exception:
        return False

if not has("whispercpp") and not has("whisper_cpp_python"):
    print("[INFO] Neither 'whispercpp' nor 'whisper-cpp-python' is installed. Attempting to install 'whispercpp'…")
    code = subprocess.call([sys.executable, "-m", "pip", "install", "whispercpp"])
    if code != 0:
        print("[WARN] 'whispercpp' install failed, trying 'whisper-cpp-python'…")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "whisper-cpp-python"])
PY

print_success "Python packages installed."

# ---------- spaCy model ----------
print_status "Downloading spaCy model (en_core_web_sm)…"
python -m spacy download en_core_web_sm
print_success "spaCy model installed."

# ---------- Ensure .env has essential keys ----------
print_status "Ensuring .env exists with required keys…"
touch .env

ensure_env () {
  local KEY="$1"
  local DEFAULT_VAL="$2"
  if ! grep -qE "^${KEY}=.*" .env; then
    echo "${KEY}=${DEFAULT_VAL}" >> .env
    print_status "Added ${KEY} to .env"
  fi
}

# Set defaults if missing (your token + tiny.en for speed on mini-PC)
ensure_env "DISCORD_TOKEN" "your_token_here"
ensure_env "WHISPER_MODEL" "tiny.en"

print_success ".env is configured (DISCORD_TOKEN, WHISPER_MODEL)."
echo -e "${YELLOW}Current .env:${NC}"
grep -E "^(DISCORD_TOKEN|WHISPER_MODEL)=" .env || true
echo

# ---------- Verify ffmpeg ----------
print_status "Verifying ffmpeg availability…"
ffmpeg -hide_banner -loglevel error -version >/dev/null 2>&1 && \
  print_success "ffmpeg is available." || {
    print_error "ffmpeg not found on PATH after install."
    exit 1
}

# ---------- Quick backend self-test ----------
print_status "Running quick whisper backend self-test…"
python - <<'PY'
import asyncio, os
try:
    from whisper_utils import get_transcriber
except Exception as e:
    print("[ERROR] Could not import whisper_utils:", e)
    raise SystemExit(1)

async def main():
    tr = await get_transcriber()  # uses WHISPER_MODEL if available
    stats = tr.get_performance_stats()
    print("[INFO] Backend    :", stats.get("backend"))
    print("[INFO] Model      :", stats.get("model_size"))
    print("[INFO] Model ready:", stats.get("model_loaded"))
    print("[INFO] Available  :", stats.get("whisper_available"))

asyncio.run(main())
PY
print_success "Backend self-test completed."

# ---------- Deactivate ----------
deactivate || true

# ---------- Final notes ----------
echo
print_success "Setup complete! 🎉"
echo -e "${YELLOW}Speech stack:${NC}"
echo "  • Primary: whisper.cpp (offline, CPU-friendly)."
echo "  • Fallback: Google Speech Recognition (only if whisper.cpp not available)."
echo
echo -e "${YELLOW}Three-layer text correction:${NC}"
echo "  1) Transcription (whisper.cpp @ 16 kHz mono)"
echo "  2) Real-time spaCy phrase corrections"
echo "  3) Fuzzy word matching via thefuzz"
echo
echo -e "${YELLOW}Intel N95 tips:${NC}"
echo "  • WHISPER_MODEL=tiny.en (fast)  — change to base/base.en for more accuracy."
echo "  • Make sure voice receive uses 48kHz PCM; bot resamples to 16kHz mono."
echo
echo -e "${YELLOW}Next steps:${NC}"
echo "  1) Put your real token into .env (DISCORD_TOKEN=...)"
echo "  2) Start the bot: ./start.sh    (or: source venv/bin/activate && python bot.py)"
echo "  3) In Discord: /setup → click Start Recording"
echo "  4) Check /transcription_stats to confirm backend = whispercpp, model = tiny.en"
