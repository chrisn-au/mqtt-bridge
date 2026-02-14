#!/usr/bin/env bash
# =============================================================================
# Build & Install: open-modbusgateway (openmmg) on Raspberry Pi
# =============================================================================
# This script:
#   1. Installs build dependencies (libmodbus, libmosquitto, autotools, etc.)
#   2. Clones the open-modbusgateway repository
#   3. Builds the 'openmmg' binary natively on the Pi
#   4. Installs it to /usr/local/bin
#   5. Creates a default config and systemd service
#
# Tested on: Raspberry Pi OS (Bookworm/Bullseye), Ubuntu for Pi
# Usage:     chmod +x build_openmmg.sh && sudo ./build_openmmg.sh
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log()   { echo -e "${GREEN}[+]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✗]${NC} $*"; exit 1; }

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
if [ "$EUID" -ne 0 ]; then
    error "Please run as root: sudo ./build_openmmg.sh"
fi

log "Starting open-modbusgateway build for Raspberry Pi"
log "Architecture: $(uname -m)"
log "OS: $(cat /etc/os-release | grep PRETTY_NAME | cut -d= -f2)"

# ---------------------------------------------------------------------------
# Step 1: Install dependencies
# ---------------------------------------------------------------------------
log "Step 1/6: Installing build dependencies..."
apt-get update -qq
apt-get install -y --no-install-recommends \
    build-essential \
    autoconf \
    automake \
    pkg-config \
    git \
    libmodbus-dev \
    libmosquitto-dev \
    libssl-dev

log "Dependencies installed ✓"

# ---------------------------------------------------------------------------
# Step 2: Clone the repository
# ---------------------------------------------------------------------------
BUILD_DIR="/tmp/open-modbusgateway-build"
REPO_URL="https://github.com/ganehag/open-modbusgateway.git"

log "Step 2/6: Cloning repository..."
rm -rf "$BUILD_DIR"
git clone --depth 1 "$REPO_URL" "$BUILD_DIR"
cd "$BUILD_DIR"
log "Repository cloned ✓"

# ---------------------------------------------------------------------------
# Step 3: Configure
# ---------------------------------------------------------------------------
log "Step 3/6: Running configure..."

# The repo ships a pre-generated configure script
if [ -f configure ]; then
    chmod +x configure
    ./configure
elif [ -f configure.ac ]; then
    # Fallback: regenerate autotools
    warn "No configure script found, regenerating with autoreconf..."
    autoreconf -fi
    ./configure
else
    error "No configure.ac or configure script found!"
fi

log "Configure complete ✓"

# ---------------------------------------------------------------------------
# Step 4: Build
# ---------------------------------------------------------------------------
log "Step 4/6: Building openmmg..."
NPROC=$(nproc)
make -j"$NPROC"
log "Build complete ✓"

# ---------------------------------------------------------------------------
# Step 5: Install binary
# ---------------------------------------------------------------------------
INSTALL_DIR="/usr/local/bin"
CONFIG_DIR="/etc/openmmg"
BINARY="src/openmmg"

if [ ! -f "$BINARY" ]; then
    # Try alternate locations
    BINARY=$(find . -name "openmmg" -type f -executable 2>/dev/null | head -1)
    if [ -z "$BINARY" ]; then
        error "Cannot find compiled openmmg binary!"
    fi
fi

log "Step 5/6: Installing binary to ${INSTALL_DIR}..."
install -m 755 "$BINARY" "${INSTALL_DIR}/openmmg"
log "Installed: ${INSTALL_DIR}/openmmg ✓"

# ---------------------------------------------------------------------------
# Step 6: Create default config & systemd service
# ---------------------------------------------------------------------------
log "Step 6/6: Setting up config and systemd service..."

mkdir -p "$CONFIG_DIR"

if [ ! -f "${CONFIG_DIR}/openmmg.conf" ]; then
    cat > "${CONFIG_DIR}/openmmg.conf" << 'CONF'
# ===========================================================================
# open-modbusgateway (openmmg) configuration
# ===========================================================================
# Docs: https://github.com/ganehag/open-modbusgateway

config mqtt
	option host '127.0.0.1'
	option port '1883'
	option keepalive '60'
	option username ''
	option password ''
	option qos '0'
	option retain 'false'
	option clean_session 'true'
	option request_topic 'modbus/request'
	option response_topic 'modbus/response'
	# TLS (uncomment and set paths to enable)
	# option ca_cert_path '/etc/openmmg/certs/ca.crt'
	# option cert_path '/etc/openmmg/certs/client.crt'
	# option key_path '/etc/openmmg/certs/client.key'

# Serial RTU gateway definition
# Use format "1" in MQTT requests to target this serial port
config serial_gateway
	option id '0'
	option device '/dev/ttyUSB0'
	option baudrate '9600'
	option parity 'none'
	option data_bits '8'
	option stop_bits '1'
	# Optionally lock to a specific slave ID:
	# option slave_id '1'

# Add more serial ports if needed:
# config serial_gateway
# 	option id '1'
# 	option device '/dev/ttyAMA0'
# 	option baudrate '19200'
# 	option parity 'even'
# 	option data_bits '8'
# 	option stop_bits '1'

# Security rules — each rule whitelists a specific target
# For serial gateways, rules are optional
# For TCP targets, at least one rule must match

config rule
	option ip '::ffff:127.0.0.1/128'
	option port '502'
	option slave_id '1'
	option function '3'
	option register_address '0-65535'

# Allow all standard read functions to slave 1 on local network
# config rule
# 	option ip '::ffff:192.168.1.0/120'
# 	option port '502'
# 	option slave_id '1'
# 	option function '1'
# 	option register_address '0-65535'
#
# config rule
# 	option ip '::ffff:192.168.1.0/120'
# 	option port '502'
# 	option slave_id '1'
# 	option function '4'
# 	option register_address '0-65535'
CONF
    log "Default config created: ${CONFIG_DIR}/openmmg.conf"
else
    warn "Config already exists, not overwriting: ${CONFIG_DIR}/openmmg.conf"
fi

# Systemd service
cat > /etc/systemd/system/openmmg.service << 'SERVICE'
[Unit]
Description=Open MQTT to Modbus Gateway (openmmg)
After=network.target mosquitto.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/openmmg -c /etc/openmmg/openmmg.conf
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

# Security hardening
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/dev
PrivateTmp=yes

# Allow access to serial devices
SupplementaryGroups=dialout

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
log "Systemd service created: openmmg.service ✓"

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
log "Cleaning up build directory..."
rm -rf "$BUILD_DIR"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo -e "${GREEN} open-modbusgateway installed successfully!${NC}"
echo "============================================================"
echo ""
echo "  Binary:    ${INSTALL_DIR}/openmmg"
echo "  Config:    ${CONFIG_DIR}/openmmg.conf"
echo "  Service:   openmmg.service"
echo ""
echo "  Next steps:"
echo "    1. Edit the config:"
echo "       sudo nano ${CONFIG_DIR}/openmmg.conf"
echo ""
echo "    2. Set your MQTT broker, serial port, and security rules"
echo ""
echo "    3. Start the service:"
echo "       sudo systemctl enable --now openmmg"
echo ""
echo "    4. Check status:"
echo "       sudo systemctl status openmmg"
echo "       sudo journalctl -u openmmg -f"
echo ""
echo "  Quick test (read 5 holding registers from slave 1 via serial):"
echo '    mosquitto_pub -t "modbus/request" -m "1 12345 0 5 1 3 1 5"'
echo '    mosquitto_sub -t "modbus/response"'
echo ""
echo "  MQTT Request Format (serial/RTU):"
echo '    1 <COOKIE> <SERIAL_ID> <TIMEOUT> <SLAVE_ID> <FUNC> <REG> <COUNT>'
echo ""
echo "  MQTT Request Format (TCP):"
echo '    0 <COOKIE> <IP_TYPE> <IP> <PORT> <TIMEOUT> <SLAVE_ID> <FUNC> <REG> <COUNT>'
echo ""
echo "============================================================"
