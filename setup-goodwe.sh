#!/bin/bash
set -e

# GoodWe MQTT Bridge - Setup Wizard
# Works on macOS, Linux, Raspberry Pi

echo ""
echo "=========================================="
echo "  GoodWe Solar Inverter - MQTT Bridge"
echo "=========================================="
echo ""

# ── Check Python ──────────────────────────────

if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found."
    echo ""
    echo "  macOS:   brew install python3"
    echo "  Ubuntu:  sudo apt install python3 python3-venv"
    echo ""
    exit 1
fi

PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "  Python version: $PY_VER"

# ── Create venv + install deps ────────────────

if [ ! -d ".venv" ]; then
    echo "  Creating virtual environment..."
    python3 -m venv .venv
fi

echo "  Installing dependencies..."
.venv/bin/pip install --quiet --disable-pip-version-check goodwe paho-mqtt
echo "  Done."
echo ""

# ── If config already exists, offer to reconfigure ──

if [ -f "goodwe.json" ]; then
    echo "  goodwe.json already exists."
    read -p "  Reconfigure? (y/N) " RECONF
    if [[ ! "$RECONF" =~ ^[Yy] ]]; then
        echo ""
        echo "  Setup complete. Run with:"
        echo "    .venv/bin/python goodwe_mqtt.py -c goodwe.json"
        echo ""
        exit 0
    fi
    echo ""
fi

# ── Inverter setup ────────────────────────────

echo "── Inverter Setup ──"
echo ""
echo "  You need your GoodWe inverter's IP address."
echo "  Find it in your router's device list or the"
echo "  GoodWe SEMS app under device info."
echo ""

INVERTERS="["
INV_COUNT=0
ADD_MORE=true

while $ADD_MORE; do
    read -p "  Inverter IP address: " INV_HOST
    if [ -z "$INV_HOST" ]; then
        if [ $INV_COUNT -eq 0 ]; then
            echo "  At least one inverter is required."
            continue
        fi
        break
    fi

    read -p "  Inverter name (e.g. Roof, optional): " INV_NAME
    INV_NAME=${INV_NAME:-"Inverter $INV_COUNT"}

    read -p "  Port (8899 for WiFi dongle, 502 for LAN dongle) [8899]: " INV_PORT
    INV_PORT=${INV_PORT:-8899}

    if [ $INV_COUNT -gt 0 ]; then
        INVERTERS="$INVERTERS,"
    fi
    INVERTERS="$INVERTERS
    {\"id\": \"$INV_COUNT\", \"name\": \"$INV_NAME\", \"host\": \"$INV_HOST\", \"port\": $INV_PORT}"
    INV_COUNT=$((INV_COUNT + 1))

    read -p "  Add another inverter? (y/N) " MORE
    if [[ ! "$MORE" =~ ^[Yy] ]]; then
        ADD_MORE=false
    fi
done

INVERTERS="$INVERTERS
  ]"

echo ""

# ── MQTT broker setup ─────────────────────────

echo "── MQTT Broker Setup ──"
echo ""
echo "  The bridge publishes inverter data to an MQTT broker."
echo "  If you don't have one yet, install mosquitto:"
echo ""
echo "    macOS:   brew install mosquitto && brew services start mosquitto"
echo "    Ubuntu:  sudo apt install mosquitto"
echo ""

read -p "  MQTT broker host [127.0.0.1]: " MQTT_HOST
MQTT_HOST=${MQTT_HOST:-127.0.0.1}

# Detect TLS from common ports/hostnames
DEFAULT_PORT=1883
USE_TLS=false
if echo "$MQTT_HOST" | grep -qiE '\.cloud|\.hivemq|\.emqx|\.azure|\.aws|\.io$'; then
    DEFAULT_PORT=8883
    USE_TLS=true
fi

read -p "  MQTT port [$DEFAULT_PORT]: " MQTT_PORT
MQTT_PORT=${MQTT_PORT:-$DEFAULT_PORT}

if [ "$MQTT_PORT" = "8883" ] && [ "$USE_TLS" = "false" ]; then
    USE_TLS=true
fi

# Authentication
echo ""
read -p "  MQTT username (leave blank if none): " MQTT_USER
MQTT_PASS=""
if [ -n "$MQTT_USER" ]; then
    read -sp "  MQTT password: " MQTT_PASS
    echo ""
fi

# TLS
if [ "$USE_TLS" = "true" ]; then
    echo ""
    echo "  TLS will be enabled (port $MQTT_PORT)."
    echo "  The system CA bundle will be used by default."
    read -p "  Custom CA certificate path (leave blank for system default): " CA_CERT
else
    echo ""
    read -p "  Enable TLS? (y/N) " ENABLE_TLS
    CA_CERT=""
    if [[ "$ENABLE_TLS" =~ ^[Yy] ]]; then
        USE_TLS=true
        read -p "  Custom CA certificate path (leave blank for system default): " CA_CERT
    fi
fi

echo ""

# ── Poll interval ─────────────────────────────

read -p "  Poll interval in seconds [30]: " POLL_INT
POLL_INT=${POLL_INT:-30}

echo ""

# ── Write config ──────────────────────────────

cat > goodwe.json << ENDCONFIG
{
  "inverters": $INVERTERS,
  "poll_interval": $POLL_INT,
  "request_topic": "goodwe/request",
  "response_topic": "goodwe/response",
  "mqtt": {
    "host": "$MQTT_HOST",
    "port": $MQTT_PORT,
    "username": "$MQTT_USER",
    "password": "$MQTT_PASS",
    "tls": $USE_TLS,
    "ca_cert": "$CA_CERT"
  }
}
ENDCONFIG

echo "=========================================="
echo "  Setup complete!"
echo "=========================================="
echo ""
echo "  Config saved to goodwe.json"
echo ""
echo "  To start the bridge:"
echo ""
echo "    .venv/bin/python goodwe_mqtt.py -c goodwe.json"
echo ""
echo "  To see inverter data on MQTT:"
echo ""
echo "    mosquitto_sub -h $MQTT_HOST -p $MQTT_PORT -t 'goodwe/response'${MQTT_USER:+ -u $MQTT_USER -P '***'}"
echo ""
echo "  To read registers on demand:"
echo ""
echo "    mosquitto_pub -h $MQTT_HOST -p $MQTT_PORT -t 'goodwe/request' \\"
echo "      -m '12345 0 3 35100 10'${MQTT_USER:+ -u $MQTT_USER -P '***'}"
echo ""
