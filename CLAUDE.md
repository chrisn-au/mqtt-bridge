# Pi Modbus Gateway Project

## What We're Building
Building and deploying open-modbusgateway (openmmg) on a Raspberry Pi.
Repo: https://github.com/ganehag/open-modbusgateway

openmmg is a C binary that bridges MQTT ↔ Modbus (TCP and serial RTU).
It depends on libmodbus and libmosquitto. It uses autotools to build.

## Target Machine
- Raspberry Pi accessible via SSH
- SSH command: `ssh chris@mqtt-bridge.local`
- OS: Raspberry Pi OS (64-bit, aarch64, Bookworm)
- Serial port: /dev/ttyUSB0 (USB-to-RS485 adapter)

## MQTT Broker
- Running locally on the Pi (mosquitto)
- Host: 127.0.0.1, Port: 1883

## How to Execute Commands on the Pi
Always use SSH from this machine. Never try to build locally.
- Single command: `ssh chris@mqtt-bridge.local '<command>'`
- Multi-line script:
  ```
  ssh chris@mqtt-bridge.local 'bash -s' << 'REMOTE'
  command1
  command2
  REMOTE
  ```
- File transfer to Pi: `scp localfile chris@mqtt-bridge.local:/remote/path`
- File transfer from Pi: `scp chris@mqtt-bridge.local:/remote/path localfile`

## Build Dependencies (apt packages on Pi)
build-essential autoconf automake pkg-config git
libmodbus-dev libmosquitto-dev libssl-dev
mosquitto mosquitto-clients

## Build Steps
1. Clone: `git clone https://github.com/ganehag/open-modbusgateway.git ~/open-modbusgateway`
2. Configure: `cd ~/open-modbusgateway && ./configure`
   - If configure fails, try `autoreconf -fi` first
3. Build: `make -j$(nproc)`
4. Install: `sudo install -m 755 src/openmmg /usr/local/bin/openmmg`
5. Config: create /etc/openmmg/openmmg.conf (see below)
6. Service: create systemd unit, enable and start

## openmmg Config Format
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

## MQTT Protocol (serial RTU requests)
Format: `1 <COOKIE> <SERIAL_ID> <TIMEOUT> <SLAVE_ID> <FUNC> <REG> <COUNT> [DATA]`
- COOKIE: unique 64-bit integer to match request/response
- SERIAL_ID: matches `option id` in serial_gateway config
- Functions: 1(read coils), 2(read discrete), 3(read holding), 4(read input), 5(write coil), 6(write reg), 15(write coils), 16(write regs)

Example: `1 99001 0 5 1 3 1 5` = read 5 holding registers from slave 1, serial port 0
Response: `99001 OK 100 200 300 400 500`

## Testing
```bash
# Subscribe for responses
mosquitto_sub -t "modbus/response" &
# Send a read request
mosquitto_pub -t "modbus/request" -m "1 12345 0 5 1 3 1 5"
```

## Sub-Agents Available
- **pi-builder**: For building, installing, configuring on the Pi
- **pi-monitor**: For checking status, logs, testing, debugging

## Important Rules
- ALWAYS use SSH to run commands on the Pi
- Check exit codes after each step
- If build fails, read the error output carefully before retrying
- The Pi user is 'chris' and has sudo access
- Serial ports need 'dialout' group membership
