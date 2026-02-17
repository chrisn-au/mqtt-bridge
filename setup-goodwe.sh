#!/bin/bash
set -e

# GoodWe MQTT Bridge - Quick Setup
# Works on macOS, Linux, Raspberry Pi

echo "=== GoodWe MQTT Bridge Setup ==="
echo ""

# Check Python 3
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found. Install Python 3.9+ first."
    exit 1
fi

PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "Python: $PY_VER"

# Create venv
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

echo "Installing dependencies..."
.venv/bin/pip install --quiet --disable-pip-version-check goodwe paho-mqtt

# Create config from example if needed
if [ ! -f "goodwe.json" ]; then
    cp goodwe.json.example goodwe.json
    echo ""
    echo "Created goodwe.json - edit it with your inverter IP address:"
    echo "  nano goodwe.json"
    echo ""
else
    echo "goodwe.json already exists, keeping it."
fi

echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit goodwe.json - set your inverter's IP address"
echo "  2. Make sure you have an MQTT broker running (e.g. brew install mosquitto)"
echo "  3. Run the bridge:"
echo ""
echo "     .venv/bin/python goodwe_mqtt.py -c goodwe.json --mqtt-host 127.0.0.1"
echo ""
