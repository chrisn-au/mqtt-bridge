#!/bin/bash
# GoodWe Inverter - MQTT Query Tool
#
# Auto-detects inverter model (ES/EM/BP vs D-NS/DT/MS/XS) and queries
# the correct registers. Reads broker settings from goodwe.json.
#
# Usage:
#   ./goodwe-query.sh                  # Auto-detect model, read all
#   ./goodwe-query.sh info             # Show inverter model/serial
#   ./goodwe-query.sh pv               # Read PV data only
#   ./goodwe-query.sh battery          # Read battery data (ES only)
#   ./goodwe-query.sh grid             # Read grid data
#   ./goodwe-query.sh energy           # Read energy totals
#   ./goodwe-query.sh settings         # Read inverter settings
#   ./goodwe-query.sh write 47511 1    # Write value to register

set -e

CONFIG="${GOODWE_CONFIG:-goodwe.json}"

# ── Load config from goodwe.json ─────────────

if [ ! -f "$CONFIG" ]; then
    echo "ERROR: Config file '$CONFIG' not found."
    echo "Run ./setup-goodwe.sh first, or set GOODWE_CONFIG=/path/to/goodwe.json"
    exit 1
fi

if command -v jq &>/dev/null; then
    GATEWAY_ID=$(jq -r '.gateway_id // ""' "$CONFIG")
    MQTT_HOST=$(jq -r '.mqtt.host // "127.0.0.1"' "$CONFIG")
    MQTT_PORT=$(jq -r '.mqtt.port // 1883' "$CONFIG")
    MQTT_USER=$(jq -r '.mqtt.username // ""' "$CONFIG")
    MQTT_PASS=$(jq -r '.mqtt.password // ""' "$CONFIG")
    MQTT_TLS=$(jq -r '.mqtt.tls // false' "$CONFIG")
    MQTT_CLIENT_ID=$(jq -r '.mqtt.client_id // ""' "$CONFIG")
    REQ_TOPIC=$(jq -r '.request_topic // "goodwe/request"' "$CONFIG")
    RESP_TOPIC=$(jq -r '.response_topic // "goodwe/response"' "$CONFIG")
    INV_ID="${GOODWE_INV_ID:-$(jq -r '.inverters[0].id // "0"' "$CONFIG")}"
elif command -v python3 &>/dev/null; then
    eval "$(python3 -c "
import json, sys
with open('$CONFIG') as f: c = json.load(f)
m = c.get('mqtt', {})
print(f'GATEWAY_ID=\"{c.get(\"gateway_id\", \"\")}\"')
print(f'MQTT_HOST=\"{m.get(\"host\", \"127.0.0.1\")}\"')
print(f'MQTT_PORT=\"{m.get(\"port\", 1883)}\"')
print(f'MQTT_USER=\"{m.get(\"username\", \"\")}\"')
print(f'MQTT_PASS=\"{m.get(\"password\", \"\")}\"')
print(f'MQTT_TLS=\"{str(m.get(\"tls\", False)).lower()}\"')
print(f'MQTT_CLIENT_ID=\"{m.get(\"client_id\", \"\")}\"')
print(f'REQ_TOPIC=\"{c.get(\"request_topic\", \"goodwe/request\")}\"')
print(f'RESP_TOPIC=\"{c.get(\"response_topic\", \"goodwe/response\")}\"')
inv = c.get('inverters', [{}])
print(f'INV_ID=\"{inv[0].get(\"id\", \"0\") if inv else \"0\"}\"')
")"
    INV_ID="${GOODWE_INV_ID:-$INV_ID}"
else
    echo "ERROR: Need jq or python3 to parse config."
    exit 1
fi

# Resolve gateway_id (default to hostname) and substitute topic variables
GATEWAY_ID="${GATEWAY_ID:-$(hostname -s)}"
MQTT_CLIENT_ID="${MQTT_CLIENT_ID:-$GATEWAY_ID}"
REQ_TOPIC="${REQ_TOPIC//\{gateway_id\}/$GATEWAY_ID}"
REQ_TOPIC="${REQ_TOPIC//\{client_id\}/$MQTT_CLIENT_ID}"
RESP_TOPIC="${RESP_TOPIC//\{gateway_id\}/$GATEWAY_ID}"
RESP_TOPIC="${RESP_TOPIC//\{client_id\}/$MQTT_CLIENT_ID}"

# ── Build mosquitto auth/TLS args ────────────

MQTT_ARGS="-h $MQTT_HOST -p $MQTT_PORT"
if [ -n "$MQTT_USER" ]; then
    MQTT_ARGS="$MQTT_ARGS -u $MQTT_USER"
fi
if [ -n "$MQTT_PASS" ]; then
    MQTT_ARGS="$MQTT_ARGS -P $MQTT_PASS"
fi
if [ "$MQTT_TLS" = "true" ]; then
    if [ -f "/etc/ssl/cert.pem" ]; then
        MQTT_ARGS="$MQTT_ARGS --cafile /etc/ssl/cert.pem"
    elif [ -d "/etc/ssl/certs" ]; then
        MQTT_ARGS="$MQTT_ARGS --capath /etc/ssl/certs"
    elif [ -f "/opt/homebrew/etc/openssl@3/cert.pem" ]; then
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

# ── Helper: publish and wait for response ────

COOKIE_BASE=$((RANDOM * 1000 + RANDOM))

mqtt_request() {
    # Send a request and return the response line matching the cookie.
    # Skips poll messages and other responses not matching our cookie.
    local message="$1"
    local cookie
    cookie=$(echo "$message" | awk '{print $1}')

    local tmpfile
    tmpfile=$(mktemp)

    # Subscribe for up to 10 messages over 12 seconds, filter by cookie
    timeout 15 mosquitto_sub $MQTT_ARGS -t "$RESP_TOPIC" -W 12 2>/dev/null | while read -r line; do
        if echo "$line" | grep -q "^$cookie "; then
            echo "$line" > "$tmpfile"
            break
        fi
    done &
    SUB_PID=$!

    sleep 0.3
    mosquitto_pub $MQTT_ARGS -t "$REQ_TOPIC" -m "$message"

    wait $SUB_PID 2>/dev/null || true

    local response
    response=$(cat "$tmpfile" 2>/dev/null)
    rm -f "$tmpfile"
    echo "$response"
}

publish_read() {
    local label="$1"
    local reg="$2"
    local count="$3"
    local cookie=$((COOKIE_BASE++))

    echo ""
    echo "── $label ──"
    echo "  Request: $cookie $INV_ID 3 $reg $count"

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

# ── Auto-detect inverter family ──────────────

detect_family() {
    local cookie=$((COOKIE_BASE++))
    echo "  Detecting inverter model..."

    local response
    response=$(mqtt_request "$cookie $INV_ID info")

    if echo "$response" | grep -q "ERR"; then
        echo "  WARNING: Could not detect model. $response"
        echo "  Defaulting to ES family."
        FAMILY="ES"
        MODEL="unknown"
        return
    fi

    # Parse: <cookie> OK model=GW5048D-ES serial=... family=ES
    MODEL=$(echo "$response" | sed -n 's/.*model=\([^ ]*\).*/\1/p')
    SERIAL=$(echo "$response" | sed -n 's/.*serial=\([^ ]*\).*/\1/p')
    FAMILY=$(echo "$response" | sed -n 's/.*family=\([^ ]*\).*/\1/p')

    if [ -z "$FAMILY" ]; then
        echo "  WARNING: Could not parse family from response."
        echo "  Response: $response"
        echo "  Defaulting to ES family."
        FAMILY="ES"
        MODEL="unknown"
        return
    fi

    echo "  Model: $MODEL  Serial: $SERIAL  Family: $FAMILY"
}

# ── Sensor query (uses read_runtime_data via bridge) ──

query_sensors() {
    local group="$1"
    local label="$2"
    local cookie=$((COOKIE_BASE++))

    echo ""
    echo "=== $label ==="
    echo "  Request: $cookie $INV_ID $group"

    local response
    response=$(mqtt_request "$cookie $INV_ID $group")

    if [ -z "$response" ]; then
        echo "  (no response - timeout)"
        return
    fi

    # Check for error
    if echo "$response" | grep -q " ERR "; then
        echo "  $response"
        return
    fi

    # Parse key=value pairs from response: <cookie> OK key1=val1 key2=val2 ...
    local data
    data=$(echo "$response" | sed "s/^$cookie OK //")

    # Pretty-print each key=value
    for pair in $data; do
        local key="${pair%%=*}"
        local val="${pair#*=}"
        printf "  %-35s %s\n" "$key" "$val"
    done
}

# ── ES family queries (ES, EM, BP) ──────────
# Uses read_runtime_data() — works with AA55 protocol

query_es_pv()      { query_sensors "pv"      "PV Solar Panel Data ($MODEL)"; }
query_es_battery()  { query_sensors "battery" "Battery Data ($MODEL)"; }
query_es_grid()     { query_sensors "grid"    "Grid & Load Data ($MODEL)"; }
query_es_energy()   { query_sensors "energy"  "Energy Totals ($MODEL)"; }
query_es_system()   { query_sensors "system"  "System Status ($MODEL)"; }

query_es_settings() {
    echo ""
    echo "=== Inverter Settings (ES) ==="
    publish_read "Backup Supply, Off-grid Charge, Shadow Scan" 45012 8
    publish_read "Capacity, Charge V/I, Discharge I/V" 45022 12
    publish_read "DOD, Battery Activated" 45032 4
    publish_read "Grid Export Limit" 45052 4
    publish_read "Battery SOC Protection" 45056 4
    publish_read "Work Mode" 45066 4
}

query_es_eco() {
    echo ""
    echo "=== Eco Mode Settings (ES, firmware 14+) ==="
    publish_read "Eco Mode 1" 47547 4
    publish_read "Eco Mode 2" 47553 4
    publish_read "Eco Mode 3" 47559 4
    publish_read "Eco Mode 4" 47565 4
}

query_es_all() {
    echo ""
    echo "=== Full ES Inverter Dump ($MODEL) ==="
    query_sensors "all" "All Sensor Data ($MODEL)"
}

# ── DT family queries (D-NS, DT, MS, XS) ───
# Also uses read_runtime_data() via bridge

query_dt_pv()      { query_sensors "pv"      "PV Solar Panel Data ($MODEL)"; }
query_dt_grid()    { query_sensors "grid"    "Grid & AC Output ($MODEL)"; }
query_dt_system()  { query_sensors "system"  "System Status ($MODEL)"; }
query_dt_energy()  { query_sensors "energy"  "Energy Totals ($MODEL)"; }
query_dt_meter()   { query_sensors "grid"    "Meter / Grid Data ($MODEL)"; }

query_dt_settings() {
    echo ""
    echo "=== Inverter Settings (D-NS/DT) ==="
    publish_read "Shadow Scan PV1" 40326 1
    publish_read "Grid Export Enabled, Export Limit" 40327 4
    publish_read "Start/Stop/Restart" 40330 3
    publish_read "Grid Export Limit %" 40336 1
    publish_read "Grid Export HW" 40345 1
    publish_read "Shadow Scan Time" 40347 1
    publish_read "Shadow Scan PV2" 40352 1
    publish_read "Shadow Scan PV3" 40362 1
}

query_dt_all() {
    echo ""
    echo "=== Full D-NS/DT Inverter Dump ($MODEL) ==="
    query_sensors "all" "All Sensor Data ($MODEL)"
}

# ── Command handling ─────────────────────────

CMD="${1:-all}"
shift 2>/dev/null || true

echo ""
echo "GoodWe Query Tool"
echo "  Broker: $MQTT_HOST:$MQTT_PORT${MQTT_TLS:+ (TLS)}${MQTT_USER:+ user=$MQTT_USER}"
echo "  Inverter ID: $INV_ID"
echo "  Topics: $REQ_TOPIC / $RESP_TOPIC"

# For 'info' command, just query and exit
if [ "$CMD" = "info" ]; then
    detect_family
    echo ""
    echo "Done."
    exit 0
fi

# For 'write' and 'help', no need to detect
if [ "$CMD" = "write" ]; then
    if [ -z "$1" ] || [ -z "$2" ]; then
        echo ""
        echo "ERROR: Write requires register and value."
        echo ""
        echo "Usage: $0 write <register> <value>"
        echo ""
        echo "ES examples:"
        echo "  $0 write 47511 1    # Set operation mode"
        echo "  $0 write 45052 5000 # Set grid export limit to 5000W"
        echo "  $0 write 45032 80   # Set battery DOD to 80%"
        echo ""
        echo "D-NS examples:"
        echo "  $0 write 40328 5000 # Set grid export limit to 5000W"
        echo "  $0 write 40327 1    # Enable grid export"
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
    echo ""
    echo "Done."
    exit 0
fi

if [ "$CMD" = "help" ] || [ "$CMD" = "--help" ] || [ "$CMD" = "-h" ]; then
    echo ""
    echo "Usage: $0 [command]"
    echo ""
    echo "Commands:"
    echo "  all       Read all register groups (default)"
    echo "  info      Show inverter model and family"
    echo "  pv        PV solar panel data"
    echo "  battery   Battery status (ES only)"
    echo "  grid      Grid and load/AC data"
    echo "  energy    Energy totals (today / lifetime)"
    echo "  system    Temperature, errors, diagnostics"
    echo "  meter     Grid meter import/export (D-NS only)"
    echo "  settings  Inverter configuration settings"
    echo "  eco       Eco mode schedules (ES, firmware 14+)"
    echo "  write <reg> <value>   Write a register value"
    echo ""
    echo "The tool auto-detects whether your inverter is:"
    echo "  ES family (GW5048D-ES, ES/EM/BP) - hybrid with battery"
    echo "  DT family (GW5000D-NS, DT/MS/XS) - grid-tied, no battery"
    echo ""
    echo "Examples:"
    echo "  $0                    # Auto-detect and dump everything"
    echo "  $0 info               # Show model/serial/family"
    echo "  $0 pv                 # Just PV data"
    echo "  $0 battery            # Battery (ES only)"
    echo "  $0 write 40328 5000   # Set grid export limit (D-NS)"
    echo ""
    echo "Environment:"
    echo "  GOODWE_CONFIG=path    # Config file (default: goodwe.json)"
    echo ""
    exit 0
fi

# Auto-detect for everything else
detect_family

case "$CMD" in
    pv)
        case "$FAMILY" in
            ES) query_es_pv ;;
            DT) query_dt_pv ;;
            *) echo "  Unknown family '$FAMILY', trying ES..."; query_es_pv ;;
        esac
        ;;

    battery|bat)
        case "$FAMILY" in
            ES) query_es_battery ;;
            DT) echo ""; echo "  D-NS/DT inverters don't have batteries." ;;
            *) query_es_battery ;;
        esac
        ;;

    grid)
        case "$FAMILY" in
            ES) query_es_grid ;;
            DT) query_dt_grid ;;
            *) query_es_grid ;;
        esac
        ;;

    energy)
        case "$FAMILY" in
            ES) query_es_energy ;;
            DT) query_dt_energy ;;
            *) query_es_energy ;;
        esac
        ;;

    system|sys)
        case "$FAMILY" in
            ES) query_es_system ;;
            DT) query_dt_system ;;
            *) query_es_system ;;
        esac
        ;;

    meter)
        case "$FAMILY" in
            DT) query_dt_meter ;;
            ES) echo ""; echo "  ES inverters use grid commands instead. Try: $0 grid" ;;
            *) query_dt_meter ;;
        esac
        ;;

    settings)
        case "$FAMILY" in
            ES) query_es_settings ;;
            DT) query_dt_settings ;;
            *) query_es_settings ;;
        esac
        ;;

    eco)
        case "$FAMILY" in
            ES) query_es_eco ;;
            DT) echo ""; echo "  D-NS/DT inverters don't support eco modes." ;;
            *) query_es_eco ;;
        esac
        ;;

    all)
        case "$FAMILY" in
            ES) query_es_all ;;
            DT) query_dt_all ;;
            *) echo "  Unknown family '$FAMILY', trying ES..."; query_es_all ;;
        esac
        ;;

    *)
        echo ""
        echo "  Unknown command '$CMD'. Run: $0 help"
        exit 1
        ;;
esac

echo ""
echo "Done."
