#!/usr/bin/env bash
# =============================================================================
# Quick Setup: Tiered Claude Code → Pi project
# =============================================================================
# Run this on your MAIN MACHINE (not the Pi).
# It creates the project folder, CLAUDE.md, and sub-agents,
# then prompts you for your Pi's IP to customize everything.
# =============================================================================

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[+]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }

PROJECT_DIR="$HOME/pi-modbus-gateway"

echo ""
echo "============================================"
echo "  Tiered Claude Code → Pi Setup"
echo "============================================"
echo ""

# Get Pi details
read -p "Enter your Pi's IP address (e.g. 192.168.1.50): " PI_IP
read -p "Enter your Pi's SSH username [pi]: " PI_USER
PI_USER=${PI_USER:-pi}

# Test SSH connection
log "Testing SSH connection to ${PI_USER}@${PI_IP}..."
if ssh -o ConnectTimeout=5 -o BatchMode=yes "${PI_USER}@${PI_IP}" "echo 'OK'" 2>/dev/null; then
    log "SSH connection successful ✓"
else
    warn "SSH connection failed. Make sure:"
    warn "  1. The Pi is powered on and connected to the network"
    warn "  2. SSH is enabled on the Pi"
    warn "  3. You've set up key-based auth: ssh-copy-id ${PI_USER}@${PI_IP}"
    echo ""
    read -p "Continue anyway? (y/n): " CONTINUE
    [[ "$CONTINUE" != "y" ]] && exit 1
fi

# Create project structure
log "Creating project at ${PROJECT_DIR}..."
mkdir -p "${PROJECT_DIR}/.claude/agents"

# Generate CLAUDE.md
log "Generating CLAUDE.md..."
sed "s/CHANGE_ME_TO_PI_IP/${PI_IP}/g; s/pi@/${PI_USER}@/g" > "${PROJECT_DIR}/CLAUDE.md" << 'CLAUDEMD'
# Pi Modbus Gateway Project

## What We're Building
Building and deploying open-modbusgateway (openmmg) on a Raspberry Pi.
Repo: https://github.com/ganehag/open-modbusgateway

openmmg is a C binary that bridges MQTT ↔ Modbus (TCP and serial RTU).
It depends on libmodbus and libmosquitto. It uses autotools to build.

## Target Machine
- Raspberry Pi accessible via SSH
- SSH command: `ssh pi@CHANGE_ME_TO_PI_IP`
- OS: Raspberry Pi OS (64-bit, aarch64)
- Serial port: /dev/ttyUSB0 (USB-to-RS485 adapter)

## How to Execute Commands on the Pi
Always use SSH. Never build locally.
- Single command: `ssh pi@CHANGE_ME_TO_PI_IP '<command>'`
- Multi-line: `ssh pi@CHANGE_ME_TO_PI_IP 'bash -s' << 'REMOTE' ... REMOTE`
- File to Pi: `scp localfile pi@CHANGE_ME_TO_PI_IP:/remote/path`

## Build Dependencies (apt on Pi)
build-essential autoconf automake pkg-config git libmodbus-dev libmosquitto-dev libssl-dev mosquitto mosquitto-clients

## Build Steps
1. Clone: `git clone https://github.com/ganehag/open-modbusgateway.git ~/open-modbusgateway`
2. Configure: `cd ~/open-modbusgateway && ./configure` (or `autoreconf -fi && ./configure`)
3. Build: `make -j$(nproc)`
4. Install: `sudo install -m 755 src/openmmg /usr/local/bin/openmmg`
5. Config: /etc/openmmg/openmmg.conf
6. Service: systemd unit → enable and start

## openmmg Config
```
config mqtt
    option host '127.0.0.1'
    option port '1883'
    option request_topic 'modbus/request'
    option response_topic 'modbus/response'

config serial_gateway
    option id '0'
    option device '/dev/ttyUSB0'
    option baudrate '9600'
    option parity 'none'
    option data_bits '8'
    option stop_bits '1'
```

## MQTT Protocol (serial RTU)
Format: `1 <COOKIE> <SERIAL_ID> <TIMEOUT> <SLAVE_ID> <FUNC> <REG> <COUNT>`
Test: `mosquitto_pub -t "modbus/request" -m "1 12345 0 5 1 3 1 5"`

## Sub-Agents
- **pi-builder**: Build, install, configure on the Pi
- **pi-monitor**: Check status, logs, test, debug

## Rules
- ALWAYS SSH into the Pi for commands
- Check exit codes after each step
- Pi user has sudo access
CLAUDEMD

# Generate pi-builder agent
log "Creating pi-builder sub-agent..."
sed "s/CHANGE_ME_TO_PI_IP/${PI_IP}/g; s/pi@/${PI_USER}@/g" > "${PROJECT_DIR}/.claude/agents/pi-builder.md" << 'AGENT1'
---
name: pi-builder
description: Builds, compiles, installs, and configures software on the Raspberry Pi via SSH. Use for any build, install, deployment, or config task on the Pi.
tools: Bash, Read, Write, Glob, Grep
model: sonnet
---

You are a build engineer for C projects on Raspberry Pi / ARM64 Linux.

## Environment
- Execute ALL commands via SSH: `ssh pi@CHANGE_ME_TO_PI_IP '<command>'`
- NEVER run build commands locally
- Pi runs Raspberry Pi OS (Debian-based, aarch64), user pi has sudo

## Build open-modbusgateway
1. Deps: `ssh pi@CHANGE_ME_TO_PI_IP 'sudo apt-get update && sudo apt-get install -y build-essential autoconf automake pkg-config git libmodbus-dev libmosquitto-dev libssl-dev'`
2. Clone: `ssh pi@CHANGE_ME_TO_PI_IP 'git clone https://github.com/ganehag/open-modbusgateway.git ~/open-modbusgateway'`
3. Configure: `ssh pi@CHANGE_ME_TO_PI_IP 'cd ~/open-modbusgateway && ./configure'`
   - If fails: `ssh pi@CHANGE_ME_TO_PI_IP 'cd ~/open-modbusgateway && autoreconf -fi && ./configure'`
4. Build: `ssh pi@CHANGE_ME_TO_PI_IP 'cd ~/open-modbusgateway && make -j$(nproc)'`
5. Install: `ssh pi@CHANGE_ME_TO_PI_IP 'sudo install -m 755 ~/open-modbusgateway/src/openmmg /usr/local/bin/openmmg'`

## Creating Files on Pi
```bash
ssh pi@CHANGE_ME_TO_PI_IP 'sudo mkdir -p /etc/openmmg && sudo tee /etc/openmmg/openmmg.conf > /dev/null' << 'CONF'
config mqtt
    option host '127.0.0.1'
    ...
CONF
```

## Error Handling
- Missing library → install the -dev package
- Repo exists → git pull instead of clone
- Always verify with exit code checks

## Report Back
Binary location, build warnings, next steps needed.
AGENT1

# Generate pi-monitor agent
log "Creating pi-monitor sub-agent..."
sed "s/CHANGE_ME_TO_PI_IP/${PI_IP}/g; s/pi@/${PI_USER}@/g" > "${PROJECT_DIR}/.claude/agents/pi-monitor.md" << 'AGENT2'
---
name: pi-monitor
description: Monitors, tests, and debugs services on the Raspberry Pi via SSH. Use for status checks, log reading, MQTT testing, and troubleshooting.
tools: Bash, Read, Grep
model: sonnet
---

You are a systems monitoring specialist for Raspberry Pi deployments.

## Environment
- Execute ALL commands via SSH: `ssh pi@CHANGE_ME_TO_PI_IP '<command>'`
- NEVER run commands locally

## Key Commands
- Status: `ssh pi@CHANGE_ME_TO_PI_IP 'sudo systemctl status openmmg'`
- Logs: `ssh pi@CHANGE_ME_TO_PI_IP 'sudo journalctl -u openmmg --no-pager -n 50'`
- Serial: `ssh pi@CHANGE_ME_TO_PI_IP 'ls -la /dev/ttyUSB* 2>/dev/null'`
- MQTT: `ssh pi@CHANGE_ME_TO_PI_IP 'sudo systemctl status mosquitto'`
- Resources: `ssh pi@CHANGE_ME_TO_PI_IP 'free -h && uptime'`

## MQTT Test
```bash
ssh pi@CHANGE_ME_TO_PI_IP 'bash -s' << 'TEST'
timeout 5 mosquitto_sub -t "modbus/response" > /tmp/mqtt_test.txt 2>&1 &
sleep 1
mosquitto_pub -t "modbus/request" -m "1 12345 0 5 1 3 1 5"
sleep 3
cat /tmp/mqtt_test.txt
rm -f /tmp/mqtt_test.txt
TEST
```

## Troubleshooting
- Service won't start → check config, serial port, permissions
- No MQTT response → check topics match, broker running
- Modbus timeout → check wiring, baudrate, slave ID, parity
- Permission denied → `sudo usermod -a -G dialout pi` (re-login needed)

## Report Back
Service state, errors found, test results, fix recommendations.
AGENT2

log "Project created ✓"

echo ""
echo "============================================"
echo -e "${GREEN}  Setup complete!${NC}"
echo "============================================"
echo ""
echo "  Project:    ${PROJECT_DIR}"
echo "  Target Pi:  ${PI_USER}@${PI_IP}"
echo ""
echo "  To get started:"
echo "    cd ${PROJECT_DIR}"
echo "    claude"
echo ""
echo "  Then tell Claude Code:"
echo '    "Use the pi-builder agent to build open-modbusgateway on the Pi"'
echo ""
echo "============================================"
