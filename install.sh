#!/bin/bash

echo "Setting up LeoScribeBot on Linux..."

# Update system
sudo apt update && sudo apt upgrade -y

# Install Python and required system packages
sudo apt install -y python3 python3-pip python3-venv ffmpeg libffi-dev libnacl-dev

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Create logs directory
mkdir -p logs

# Install PM2 if not already installed
if ! command -v pm2 &> /dev/null; then
    echo "Installing PM2..."
    curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
    sudo apt-get install -y nodejs
    sudo npm install -g pm2
fi

echo "Setup complete! Don't forget to:"
echo "1. Add your Discord token to .env file"
echo "2. Run: pm2 start ecosystem.config.js"
echo "3. Run: pm2 save && pm2 startup"
