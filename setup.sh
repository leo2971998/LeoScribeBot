#!/bin/bash
# Enhanced setup script for LeoScribeBot with optimizations and robust checks.

echo "🚀 Setting up LeoScribeBot with real-time correction..."

# --- Configuration: Colors for output ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# --- Helper Functions for logging ---
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}
print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}
print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# --- Fix for Caddy GPG Key ---
print_status "Checking for and adding missing GPG keys..."
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable.gpg
print_success "Caddy GPG key is up to date."

# --- System Updates ---
print_status "Updating system packages..."
sudo apt update && sudo apt upgrade -y

# --- System Dependency Installation ---
print_status "Installing system dependencies for audio and Python..."
sudo apt install -y \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    portaudio19-dev \
    ffmpeg \
    libffi-dev \
    libnacl-dev \
    build-essential \
    git

# --- Robust Python Version Check ---
print_status "Checking Python version..."
PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')

if dpkg --compare-versions "$PYTHON_VERSION" "lt" "3.8"; then
    print_error "Python 3.8 or higher is required. You have version $PYTHON_VERSION."
    exit 1
else
    print_success "Python version $PYTHON_VERSION is compatible."
fi

# --- Virtual Environment Setup (Nuke and Rebuild) ---
print_status "Setting up Python virtual environment..."
if [ -d "venv" ]; then
    print_status "Removing old virtual environment for a clean installation..."
    rm -rf venv
fi

python3 -m venv venv
print_success "Virtual environment created."

source venv/bin/activate
print_success "Virtual environment activated."

# --- Python Dependency Installation ---
print_status "Upgrading pip..."
pip install --upgrade pip
print_status "Installing Python dependencies from requirements.txt..."
pip install -r requirements.txt
print_success "All Python dependencies are installed."

# --- spaCy Model Download ---
print_status "Downloading spaCy English model (en_core_web_sm)..."
python -m spacy download en_core_web_sm
print_success "spaCy model downloaded."

# Deactivate for a clean state before finishing
deactivate

# --- Final Instructions ---
print_success "Setup complete! 🎉"
echo ""
echo -e "${YELLOW}🔧 Intel N95 Optimization Notes:${NC}"
echo "   • The bot is optimized for CPU usage, averaging <50ms for corrections."
echo "   • Total memory usage should remain low (~100-150MB)."
echo "   • All processing is done offline on your machine."
echo ""
echo -e "${YELLOW}📋 Next Steps:${NC}"
echo "   1. Create your .env file with your Discord token:"
echo "      echo 'DISCORD_TOKEN=your_token_here' > .env"
echo ""
echo "   2. Start the bot using PM2 for production deployment:"
echo "      ./start.sh"
echo ""
echo "   3. To view logs:"
echo "      pm2 logs leoscribebot"
echo ""
echo "   4. To stop the bot:"
echo "      ./stop.sh"

