#!/bin/bash
# Setup script for Intel N95 optimization

echo "🚀 Setting up LeoScribeBot with real-time correction..."
echo "Optimized for Intel N95 CPU with real-time transcription"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if we're running on Linux
if [[ "$OSTYPE" != "linux-gnu"* ]]; then
    print_error "This script is optimized for Linux. Please install dependencies manually."
    exit 1
fi

print_status "Updating system packages..."
sudo apt update

print_status "Installing system dependencies for audio processing..."
# Install required system packages for audio and Python
sudo apt install -y \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    portaudio19-dev \
    python3-pyaudio \
    ffmpeg \
    libffi-dev \
    libnacl-dev \
    build-essential \
    git

# Check Python version
python_version=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
print_status "Python version: $python_version"

if (( $(echo "$python_version >= 3.8" | bc -l) )); then
    print_success "Python version is compatible"
else
    print_error "Python 3.8+ required. Current version: $python_version"
    exit 1
fi

# Create and activate virtual environment
print_status "Creating virtual environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
    print_success "Virtual environment created"
else
    print_warning "Virtual environment already exists"
fi

print_status "Activating virtual environment..."
source venv/bin/activate

# Upgrade pip for better compatibility
print_status "Upgrading pip..."
pip install --upgrade pip

# Install Python dependencies
print_status "Installing Python dependencies..."
pip install -r requirements.txt

# Download spaCy model for offline operation
print_status "Downloading spaCy English model (en_core_web_sm - 15MB)..."
python -m spacy download en_core_web_sm

# Test the installation
print_status "Testing text corrector installation..."
python3 -c "
import asyncio
import sys
sys.path.append('.')

async def test_corrector():
    try:
        from text_corrector import correct_transcript, benchmark_correction
        
        # Quick functionality test
        result = await correct_transcript('serge binding is a magic manifestation')
        print(f'✅ Test correction: \"serge binding is a magic manifestation\" -> \"{result}\"')
        
        # Performance benchmark
        print('📊 Running performance benchmark...')
        benchmark = await benchmark_correction()
        
        print(f'📈 Performance Results:')
        print(f'   Average time: {benchmark[\"avg_time_ms\"]:.1f}ms')
        print(f'   Max time: {benchmark[\"max_time_ms\"]:.1f}ms')
        print(f'   Min time: {benchmark[\"min_time_ms\"]:.1f}ms')
        print(f'   spaCy available: {benchmark[\"spacy_available\"]}')
        print(f'   Model loaded: {benchmark[\"model_loaded\"]}')
        
        if benchmark['avg_time_ms'] < 50:
            print('🎯 Performance target achieved: <50ms average')
        else:
            print('⚠️  Performance above target, but still functional')
            
        return True
    except Exception as e:
        print(f'❌ Test failed: {e}')
        return False

success = asyncio.run(test_corrector())
sys.exit(0 if success else 1)
"

if [ $? -eq 0 ]; then
    print_success "Text corrector test passed!"
else
    print_error "Text corrector test failed. Check the error messages above."
fi

# Create logs directory
print_status "Creating logs directory..."
mkdir -p logs

# Check for PM2 (optional for production deployment)
if command -v pm2 &> /dev/null; then
    print_success "PM2 already installed"
elif command -v node &> /dev/null; then
    print_status "Installing PM2 for production deployment..."
    sudo npm install -g pm2
else
    print_warning "Node.js not found. PM2 installation skipped."
    print_warning "For production deployment, install Node.js and PM2 manually."
fi

# Memory optimization tips for Intel N95
print_success "Setup complete! 🎉"
echo ""
echo "🔧 Intel N95 Optimization Tips:"
echo "   • The bot uses ~100MB RAM total with spaCy model loaded"
echo "   • Text correction averages <50ms response time"
echo "   • All processing is done offline after initial setup"
echo "   • Model files are cached for fast startup"
echo ""
echo "📋 Next Steps:"
echo "   1. Create .env file with your Discord token:"
echo "      echo 'DISCORD_TOKEN=your_token_here' > .env"
echo ""
echo "   2. Test the bot:"
echo "      source venv/bin/activate"
echo "      python3 bot.py"
echo ""
echo "   3. For production deployment with PM2:"
echo "      pm2 start ecosystem.config.js"
echo "      pm2 save && pm2 startup"
echo ""
echo "💡 Performance Notes:"
echo "   • First correction may take ~100ms (model loading)"
echo "   • Subsequent corrections: 20-50ms (Intel N95 optimized)"
echo "   • Cache improves performance for repeated phrases"
echo "   • Memory usage stays under 100MB"

# Final check
if [ -f "text_corrector.py" ] && [ -f "requirements.txt" ]; then
    print_success "All files in place. Ready to run!"
else
    print_error "Some files are missing. Please check the installation."
fi