# Tiered Claude Code Setup: Main Machine → Raspberry Pi

A tiered architecture where Claude Code runs on your main machine and controls the Pi remotely via SSH — either directly through bash commands or via a custom sub-agent.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  YOUR MAIN MACHINE (laptop/desktop)                         │
│                                                             │
│  Claude Code (interactive)                                  │
│    ├── Main conversation & planning                         │
│    ├── Sub-agent: "pi-builder"                              │
│    │     └── SSH into Pi → build, configure, test           │
│    └── Sub-agent: "pi-monitor"                              │
│          └── SSH into Pi → check logs, status, debug        │
│                                                             │
│  You see everything, approve commands, steer the process    │
└────────────────────┬────────────────────────────────────────┘
                     │ SSH (key-based, passwordless)
                     │
┌────────────────────▼────────────────────────────────────────┐
│  RASPBERRY PI                                               │
│                                                             │
│  /dev/ttyUSB0 ──► Modbus RTU devices                       │
│  mosquitto (MQTT broker)                                    │
│  openmmg (open-modbusgateway binary)                        │
│                                                             │
│  No Claude Code needed on the Pi — it's just a target       │
└─────────────────────────────────────────────────────────────┘
```

## Why This Approach?

- **Pi stays lean** — no Node.js, no Claude Code binary, no extra RAM usage
- **Your main machine does the thinking** — Claude Code runs where you have power and a good screen
- **Sub-agents keep context clean** — Pi build steps don't pollute your main conversation
- **You stay in control** — approve every SSH command before it runs

---

## Part 1: Prepare the Pi (one-time, ~5 minutes)

SSH into your Pi from your main machine and run these:

```bash
# 1. Make sure SSH is running
sudo apt-get update
sudo apt-get install -y openssh-server
sudo systemctl enable --now ssh

# 2. Install build dependencies for open-modbusgateway
sudo apt-get install -y \
    build-essential autoconf automake pkg-config git \
    libmodbus-dev libmosquitto-dev libssl-dev \
    mosquitto mosquitto-clients

# 3. Note the Pi's IP address
hostname -I
```

### Set Up Passwordless SSH (from your main machine)

```bash
# On your main machine — generate a key if you don't have one
ssh-keygen -t ed25519 -C "claude-code-pi"

# Copy it to the Pi (replace with your Pi's IP and user)
ssh-copy-id pi@192.168.1.XXX

# Test — this should connect without a password prompt
ssh pi@192.168.1.XXX "echo 'SSH working!'"
```

---

## Part 2: Install Claude Code on Your Main Machine

### macOS / Linux:
```bash
curl -fsSL https://claude.ai/install.sh | bash
```

### Windows (PowerShell):
```bash
irm https://claude.ai/install.ps1 | iex
```

Then authenticate:
```bash
claude
# Follow the browser OAuth flow
```

---

## Part 3: Create the Project & CLAUDE.md

On your main machine:

```bash
mkdir ~/pi-modbus-gateway && cd ~/pi-modbus-gateway
```

Create the `CLAUDE.md` file (Claude Code reads this automatically for project context):

```bash
cat > CLAUDE.md << 'EOF'
# Pi Modbus Gateway Project

## What We're Building
Building and deploying open-modbusgateway (openmmg) on a Raspberry Pi.
Repo: https://github.com/ganehag/open-modbusgateway

## Target Machine
- Raspberry Pi accessible via SSH
- SSH command: `ssh pi@PI_IP_ADDRESS`
- OS: Raspberry Pi OS (64-bit, Bookworm)
- Serial port: /dev/ttyUSB0 (USB-RS485 adapter)

## MQTT Broker
- Running locally on the Pi (mosquitto)
- Host: 127.0.0.1, Port: 1883

## How to Execute Commands on the Pi
Use bash with SSH: `ssh pi@PI_IP_ADDRESS '<command>'`
For multi-line scripts: `ssh pi@PI_IP_ADDRESS 'bash -s' << 'REMOTE' ... REMOTE`

## Build Steps
1. Clone repo to ~/open-modbusgateway on the Pi
2. Run ./configure && make -j$(nproc)
3. Install binary to /usr/local/bin/openmmg
4. Create config at /etc/openmmg/openmmg.conf
5. Create and enable systemd service
6. Test with mosquitto_pub/mosquitto_sub

## Serial Gateway Config
- Device: /dev/ttyUSB0
- Baudrate: 9600
- Parity: none
- Data bits: 8
- Stop bits: 1

## Important
- Always use SSH to run commands on the Pi, never try to run locally
- Check command exit codes after each step
- If a build step fails, read the error and fix it before proceeding
EOF
```

**Edit the `CLAUDE.md`** and replace `PI_IP_ADDRESS` with your Pi's actual IP.

---

## Part 4: Create Sub-Agents

Sub-agents are specialized assistants that Claude Code can delegate to. They run in their own context window, keeping your main conversation clean.

### Create the agents directory:

```bash
mkdir -p ~/pi-modbus-gateway/.claude/agents
```

### Agent 1: pi-builder (builds and deploys on the Pi)

```bash
cat > ~/pi-modbus-gateway/.claude/agents/pi-builder.md << 'EOF'
---
name: pi-builder
description: Builds, compiles, installs, and configures software on the Raspberry Pi via SSH. Use this agent for any build, install, or deployment tasks on the Pi.
tools: Bash, Read, Write, Glob, Grep
model: sonnet
---

You are a build engineer specializing in compiling C projects on Raspberry Pi / ARM64 Linux.

## Your Environment
- You execute ALL commands on the Pi via SSH: `ssh pi@PI_IP_ADDRESS '<command>'`
- Never run build commands locally — always through SSH
- The Pi runs Raspberry Pi OS (Debian-based, aarch64)

## Your Capabilities
- Clone git repositories on the Pi
- Install apt packages (via sudo)
- Run autotools (configure, make, make install)
- Create and edit config files on the Pi
- Set up systemd services
- Debug build failures by reading error output

## Build Process for open-modbusgateway
1. `ssh pi@PI_IP_ADDRESS 'git clone https://github.com/ganehag/open-modbusgateway.git ~/open-modbusgateway'`
2. `ssh pi@PI_IP_ADDRESS 'cd ~/open-modbusgateway && ./configure'`
3. `ssh pi@PI_IP_ADDRESS 'cd ~/open-modbusgateway && make -j$(nproc)'`
4. `ssh pi@PI_IP_ADDRESS 'sudo install -m 755 ~/open-modbusgateway/src/openmmg /usr/local/bin/openmmg'`

## Error Handling
- If configure fails, check for missing -dev packages
- If make fails, read the compiler error and fix it
- Always verify each step succeeded before moving on
- Use `echo $?` after commands to check exit codes

## When Done
Report back: binary location, version if available, and any warnings encountered.
EOF
```

### Agent 2: pi-monitor (checks status, logs, tests)

```bash
cat > ~/pi-modbus-gateway/.claude/agents/pi-monitor.md << 'EOF'
---
name: pi-monitor
description: Monitors, tests, and debugs services running on the Raspberry Pi via SSH. Use for checking service status, reading logs, testing MQTT/Modbus communication, and troubleshooting.
tools: Bash, Read, Grep
model: sonnet
---

You are a systems monitoring specialist for Raspberry Pi deployments.

## Your Environment
- You execute ALL commands on the Pi via SSH: `ssh pi@PI_IP_ADDRESS '<command>'`
- Never run commands locally — always through SSH

## Monitoring Commands
- Service status: `ssh pi@PI_IP_ADDRESS 'sudo systemctl status openmmg'`
- Live logs: `ssh pi@PI_IP_ADDRESS 'sudo journalctl -u openmmg --no-pager -n 50'`
- Serial port check: `ssh pi@PI_IP_ADDRESS 'ls -la /dev/ttyUSB*'`
- MQTT broker status: `ssh pi@PI_IP_ADDRESS 'sudo systemctl status mosquitto'`
- Process check: `ssh pi@PI_IP_ADDRESS 'pgrep -a openmmg'`

## Testing Commands
- MQTT subscribe (background): `ssh pi@PI_IP_ADDRESS 'timeout 5 mosquitto_sub -t "modbus/response" &'`
- MQTT publish test: `ssh pi@PI_IP_ADDRESS 'mosquitto_pub -t "modbus/request" -m "1 12345 0 5 1 3 1 5"'`
- Network check: `ssh pi@PI_IP_ADDRESS 'ss -tlnp | grep -E "(1883|mosquitto|openmmg)"'`

## Troubleshooting
- If service won't start: check config syntax, permissions, serial port availability
- If no MQTT response: check broker is running, topics match config
- If Modbus timeout: check serial wiring, baudrate, slave ID
- Check Pi resources: `ssh pi@PI_IP_ADDRESS 'free -h && df -h && uptime'`

## When Done
Report: service state, any errors found, and recommendations.
EOF
```

**Edit both agent files** and replace `PI_IP_ADDRESS` with your Pi's actual IP.

---

## Part 5: Use It

Start Claude Code in your project:

```bash
cd ~/pi-modbus-gateway
claude
```

Now you can give it commands like:

### Build Everything
```
Use the pi-builder agent to clone open-modbusgateway on the Pi, build it, 
install it, create a config for serial RTU on /dev/ttyUSB0 at 9600 baud 
with MQTT on localhost, and set up a systemd service.
```

### Check Status
```
Use the pi-monitor agent to check if openmmg is running, show me 
the last 20 log lines, and test an MQTT request.
```

### Direct SSH (without sub-agents)
You can also just tell Claude Code directly:
```
SSH into the Pi at pi@192.168.1.50 and check if /dev/ttyUSB0 exists 
and what baudrate it's set to.
```

Claude Code will run `ssh pi@192.168.1.50 'stty -F /dev/ttyUSB0'` and show you the result.

---

## Part 6: Workflow Example

Here's what a typical session looks like:

```
You:    Build open-modbusgateway on the Pi

Claude: I'll use the pi-builder agent for this.
        [spawns pi-builder sub-agent]

        pi-builder: Running ssh pi@192.168.1.50 'git clone ...'
        > Cloning into '/home/pi/open-modbusgateway'...
        > done.

        pi-builder: Running ssh pi@192.168.1.50 'cd ~/open-modbusgateway && ./configure'
        > checking for gcc... gcc
        > checking for libmodbus... yes
        > checking for libmosquitto... yes
        > ...
        > configure: creating Makefile

        pi-builder: Running ssh pi@192.168.1.50 'cd ~/open-modbusgateway && make -j4'
        > gcc -o src/openmmg src/main.o src/mqtt.o src/modbus.o ...
        > Build complete.

        [pi-builder reports back to main Claude Code]

Claude: ✓ Build succeeded. openmmg compiled on the Pi.
        Want me to install it and set up the service?

You:    Yes, and configure it for my serial port

Claude: [spawns pi-builder again with install + config task]
        ...
```

---

## Tips

### Use tmux for Long Sessions
```bash
# On your main machine
tmux new -s modbus
cd ~/pi-modbus-gateway
claude
# Detach: Ctrl+B, D
# Reattach: tmux attach -t modbus
```

### Background Agents
While a sub-agent is working, press `Ctrl+B` in Claude Code to background it and keep working in the main session.

### Resuming Sessions
If you exit Claude Code:
```bash
cd ~/pi-modbus-gateway
claude -c    # resume last conversation
```

### Adding More Pi Devices
Create additional agents for each Pi, or parameterize the SSH target in your CLAUDE.md.

---

## Alternative: SSH MCP Server

For even deeper integration, you can use an MCP (Model Context Protocol) server that gives Claude Code SSH tools natively. There's a community project at [nqmn/adremote-mcp](https://github.com/nqmn/adremote-mcp) that does this. Add it to your `.claude/mcp.json`:

```json
{
  "ssh-remote": {
    "command": "python",
    "args": ["/path/to/ssh_mcp_server.py"]
  }
}
```

This gives Claude Code `ssh_connect`, `ssh_execute`, `ssh_upload` tools it can use directly, without wrapping everything in bash SSH commands.
