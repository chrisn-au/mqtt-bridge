#!/bin/bash
# GoodWe ES Inverter - MQTT Query Tool
#
# Publishes register read/write requests via MQTT and displays responses.
# Reads broker settings from goodwe.json (same config as the bridge).
#
# Usage:
#   ./goodwe-query.sh                  # Read all ES register groups
#   ./goodwe-query.sh pv               # Read PV data only
#   ./goodwe-query.sh battery          # Read battery data only
#   ./goodwe-query.sh grid             # Read grid data only
#   ./goodwe-query.sh settings         # Read inverter settings
#   ./goodwe-query.sh write 47511 1    # Write value 1 to register 47511

set -e

CONFIG="${GOODWE_CONFIG:-goodwe.json}"

# ── Load config from goodwe.json ─────────────

if [ ! -f "$CONFIG" ]; then
    echo "ERROR: Config file '$CONFIG' not found."
    echo "Run ./setup-goodwe.sh first, or set GOODWE_CONFIG=/path/to/goodwe.json"
    exit 1
fi

# Check for jq or python3 to parse JSON
if command -v jq &>/dev/null; then
    MQTT_HOST=$(jq -r '.mqtt.host // "127.0.0.1"' "$CONFIG")
    MQTT_PORT=$(jq -r '.mqtt.port // 1883' "$CONFIG")
    MQTT_USER=$(jq -r '.mqtt.username // ""' "$CONFIG")
    MQTT_PASS=$(jq -r '.mqtt.password // ""' "$CONFIG")
    MQTT_TLS=$(jq -r '.mqtt.tls // false' "$CONFIG")
    REQ_TOPIC=$(jq -r '.request_topic // "goodwe/request"' "$CONFIG")
    RESP_TOPIC=$(jq -r '.response_topic // "goodwe/response"' "$CONFIG")
    INV_ID=$(jq -r '.inverters[0].id // "0"' "$CONFIG")
elif command -v python3 &>/dev/null; then
    eval "$(python3 -c "
import json, sys
with open('$CONFIG') as f: c = json.load(f)
m = c.get('mqtt', {})
print(f'MQTT_HOST=\"{m.get(\"host\", \"127.0.0.1\")}\"')
print(f'MQTT_PORT=\"{m.get(\"port\", 1883)}\"')
print(f'MQTT_USER=\"{m.get(\"username\", \"\")}\"')
print(f'MQTT_PASS=\"{m.get(\"password\", \"\")}\"')
print(f'MQTT_TLS=\"{str(m.get(\"tls\", False)).lower()}\"')
print(f'REQ_TOPIC=\"{c.get(\"request_topic\", \"goodwe/request\")}\"')
print(f'RESP_TOPIC=\"{c.get(\"response_topic\", \"goodwe/response\")}\"')
inv = c.get('inverters', [{}])
print(f'INV_ID=\"{inv[0].get(\"id\", \"0\") if inv else \"0\"}\"')
")"
else
    echo "ERROR: Need jq or python3 to parse config."
    exit 1
fi

# ── Build mosquitto auth/TLS args ────────────

MQTT_ARGS="-h $MQTT_HOST -p $MQTT_PORT"
if [ -n "$MQTT_USER" ]; then
    MQTT_ARGS="$MQTT_ARGS -u $MQTT_USER"
fi
if [ -n "$MQTT_PASS" ]; then
    MQTT_ARGS="$MQTT_ARGS -P $MQTT_PASS"
fi
if [ "$MQTT_TLS" = "true" ]; then
    # Use system CA bundle
    if [ -d "/etc/ssl/certs" ]; then
        MQTT_ARGS="$MQTT_ARGS --capath /etc/ssl/certs"
    elif [ -f "/etc/ssl/cert.pem" ]; then
        MQTT_ARGS="$MQTT_ARGS --cafile /etc/ssl/cert.pem"
    else
        # macOS fallback
        MQTT_ARGS="$MQTT_ARGS --cafile /opt/homebrew/etc/openssl@3/cert.pem"
    fi
fi

# ── Check mosquitto tools ────────────────────

if ! command -v mosquitto_pub &>/dev/null; then
    echo "ERROR: mosquitto_pub not found."
    echo ""
    echo "  macOS:   brew install mosquitto"
    echo "  Ubuntu:  sudo apt install mosquitto-clients"
    echo ""
    exit 1
fi

# ── ES Register Map ─────────────────────────
#
# The goodwe library uses internal byte offsets, but the MQTT bridge
# uses the protocol's register addresses. ES runtime data starts at
# register 35100, settings at various Modbus holding registers.
#
# Runtime registers (func 3, base ~35100):
#   Offset 0-9:   PV strings (voltage, current, mode)
#   Offset 10-32: Battery (voltage, temp, current, SOC, SOH)
#   Offset 33-52: Grid & load (voltage, current, power, frequency)
#   Offset 53-89: System (temperature, errors, energy totals, diagnostics)
#
# Settings registers (func 3, base ~45000+):
#   Various configuration parameters

# ── Helper: publish and wait for response ────

COOKIE_BASE=$((RANDOM * 1000 + RANDOM))

publish_read() {
    local label="$1"
    local reg="$2"
    local count="$3"
    local cookie=$((COOKIE_BASE++))

    echo ""
    echo "── $label ──"
    echo "  Request: $cookie $INV_ID 3 $reg $count"

    # Start subscriber in background, wait for our cookie
    timeout 10 mosquitto_sub $MQTT_ARGS -t "$RESP_TOPIC" -C 1 -W 8 \
        2>/dev/null | while read -r line; do
        if echo "$line" | grep -q "^$cookie "; then
            echo "  Response: $line"
        fi
    done &
    SUB_PID=$!

    sleep 0.3
    mosquitto_pub $MQTT_ARGS -t "$REQ_TOPIC" -m "$cookie $INV_ID 3 $reg $count"

    wait $SUB_PID 2>/dev/null || true
}

publish_write() {
    local reg="$1"
    local value="$2"
    local cookie=$((COOKIE_BASE++))

    echo ""
    echo "── Write register $reg = $value ──"
    echo "  Request: $cookie $INV_ID 6 $reg 1 $value"

    timeout 10 mosquitto_sub $MQTT_ARGS -t "$RESP_TOPIC" -C 1 -W 8 \
        2>/dev/null | while read -r line; do
        if echo "$line" | grep -q "^$cookie "; then
            echo "  Response: $line"
        fi
    done &
    SUB_PID=$!

    sleep 0.3
    mosquitto_pub $MQTT_ARGS -t "$REQ_TOPIC" -m "$cookie $INV_ID 6 $reg 1 $value"

    wait $SUB_PID 2>/dev/null || true
}

# ── Command handling ─────────────────────────

CMD="${1:-all}"
shift 2>/dev/null || true

echo ""
echo "GoodWe ES Query Tool"
echo "  Broker: $MQTT_HOST:$MQTT_PORT${MQTT_TLS:+ (TLS)}${MQTT_USER:+ user=$MQTT_USER}"
echo "  Inverter ID: $INV_ID"
echo "  Topics: $REQ_TOPIC / $RESP_TOPIC"

case "$CMD" in
    pv)
        echo ""
        echo "=== PV Solar Panel Data ==="
        publish_read "PV1 Voltage, Current, Power, Mode" 35100 5
        publish_read "PV2 Voltage, Current, Power, Mode" 35105 5
        ;;

    battery|bat)
        echo ""
        echo "=== Battery Data ==="
        publish_read "Battery Voltage, Status, Temp" 35110 8
        publish_read "Battery Current, Charge/Discharge Limits" 35118 12
        publish_read "Battery SOC, SOH, Mode, Warnings" 35126 4
        ;;

    grid)
        echo ""
        echo "=== Grid & Load Data ==="
        publish_read "Meter Status, Grid V/I/P/F, Mode" 35133 10
        publish_read "Load V/I/P/F, Mode, Work Mode" 35143 10
        ;;

    energy)
        echo ""
        echo "=== Energy Totals ==="
        publish_read "Total PV, Hours, Today PV" 35159 12
        publish_read "Load Today, Load Total, Total Power" 35169 10
        ;;

    system|sys)
        echo ""
        echo "=== System Status ==="
        publish_read "Temperature, Error Codes" 35153 6
        publish_read "Work Mode, Relay, Grid In/Out" 35177 8
        publish_read "Diagnostics" 35189 4
        ;;

    settings)
        echo ""
        echo "=== Inverter Settings ==="
        publish_read "Backup Supply, Off-grid Charge, Shadow Scan" 45012 8
        publish_read "Capacity, Charge V/I, Discharge I/V" 45022 12
        publish_read "DOD, Battery Activated" 45032 4
        publish_read "Grid Export Limit" 45052 4
        publish_read "Work Mode" 45066 4
        ;;

    eco)
        echo ""
        echo "=== Eco Mode Settings (ARM firmware 14+) ==="
        publish_read "Eco Mode 1" 47547 4
        publish_read "Eco Mode 2" 47553 4
        publish_read "Eco Mode 3" 47559 4
        publish_read "Eco Mode 4" 47565 4
        ;;

    write)
        if [ -z "$1" ] || [ -z "$2" ]; then
            echo ""
            echo "ERROR: Write requires register and value."
            echo ""
            echo "Usage: $0 write <register> <value>"
            echo ""
            echo "Examples:"
            echo "  $0 write 47511 1    # Set operation mode"
            echo "  $0 write 45052 5000 # Set grid export limit to 5000W"
            echo "  $0 write 45032 80   # Set battery DOD to 80%"
            echo ""
            echo "WARNING: Writing to inverter registers can affect"
            echo "         system behaviour. Use with caution."
            echo ""
            exit 1
        fi
        echo ""
        echo "=== Write Register ==="
        echo ""
        echo "WARNING: Writing to inverter registers can affect system behaviour."
        read -p "Continue? (y/N) " CONFIRM
        if [[ ! "$CONFIRM" =~ ^[Yy] ]]; then
            echo "Cancelled."
            exit 0
        fi
        publish_write "$1" "$2"
        ;;

    all)
        echo ""
        echo "=== Full ES Inverter Dump ==="
        publish_read "PV1 Voltage, Current, Power, Mode" 35100 5
        publish_read "PV2 Voltage, Current, Power, Mode" 35105 5
        publish_read "Battery Voltage, Status, Temp" 35110 8
        publish_read "Battery Current, Limits, SOC, SOH" 35118 14
        publish_read "Grid & Meter Data" 35133 20
        publish_read "Temperature, Errors, Energy Totals" 35153 30
        publish_read "System Status & Diagnostics" 35183 10
        ;;

    *)
        echo ""
        echo "Usage: $0 [command]"
        echo ""
        echo "Commands:"
        echo "  all       Read all ES register groups (default)"
        echo "  pv        PV solar panel data"
        echo "  battery   Battery status and data"
        echo "  grid      Grid and load data"
        echo "  energy    Energy totals (today / lifetime)"
        echo "  system    Temperature, errors, diagnostics"
        echo "  settings  Inverter configuration settings"
        echo "  eco       Eco mode schedules (firmware 14+)"
        echo "  write <reg> <value>   Write a register value"
        echo ""
        echo "Examples:"
        echo "  $0                    # Dump everything"
        echo "  $0 pv                 # Just PV data"
        echo "  $0 battery            # Just battery"
        echo "  $0 write 47511 1      # Write to register"
        echo ""
        echo "Environment:"
        echo "  GOODWE_CONFIG=path    # Config file (default: goodwe.json)"
        echo ""
        exit 1
        ;;
esac

echo ""
echo "Done."
