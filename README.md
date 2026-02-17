# MQTT Bridge

A Raspberry Pi-based MQTT-to-Modbus gateway using [open-modbusgateway](https://github.com/ganehag/open-modbusgateway) (openmmg). Receives Modbus commands over MQTT, executes them against Modbus TCP or serial RTU targets, and publishes responses back. Also integrates with [GoodWe solar inverters](https://github.com/marcelblijleven/goodwe) for live monitoring via WiFi. Includes a web management UI for configuration and monitoring.

```
MQTT Broker                    Raspberry Pi                    Modbus Devices
    │                          ┌────────────┐                       │
    │  modbus/request    ───►  │  openmmg   │ ── /dev/ttyUSB0 ──► Slave 1
    │                          │            │ ── /dev/ttyAMA0 ──► Slave 2
    │  modbus/response   ◄───  │  (C binary)│                       │
    │                          └────────────┘                       │
    │                          ┌────────────┐              GoodWe Inverter
    │                          │  Web UI    │ ── WiFi/UDP ──► :8899
    │                          │  :8080     │              (or TCP :502)
    │                          └────────────┘
```

## Quick Start (GoodWe only -- Mac / Linux / Pi)

No compilation needed. Just Python 3.9+ and an MQTT broker.

```bash
git clone https://github.com/chrisn-au/mqtt-bridge.git
cd mqtt-bridge
./setup-goodwe.sh
```

Edit `goodwe.json` with your inverter's IP address, then run:

```bash
.venv/bin/python goodwe_mqtt.py -c goodwe.json --mqtt-host 127.0.0.1
```

If you don't have an MQTT broker yet:

```bash
# macOS
brew install mosquitto && brew services start mosquitto

# Debian/Ubuntu/Pi
sudo apt-get install -y mosquitto mosquitto-clients
```

See [MQTT Bridge protocol](#mqtt-bridge) below for the request/response format.

---

## Pi Setup (full Modbus gateway + web UI)

### 1. Flash Raspberry Pi OS

Flash **Raspberry Pi OS Lite (64-bit, Bookworm)** using the [Raspberry Pi Imager](https://www.raspberrypi.com/software/). In the imager settings:

- Set hostname to `mqtt-bridge`
- Enable SSH
- Set username to `chris` (or your preference)
- Configure WiFi if not using Ethernet

### 2. Connect and set the hostname

```bash
ssh chris@mqtt-bridge.local
sudo hostnamectl set-hostname mqtt-bridge
```

The Pi will be reachable at `mqtt-bridge.local` via mDNS (Avahi is included in Raspberry Pi OS by default).

### 3. Install dependencies

```bash
sudo apt-get update
sudo apt-get install -y build-essential autoconf automake pkg-config \
    git libmodbus-dev libmosquitto-dev libssl-dev \
    mosquitto mosquitto-clients python3-venv
```

### 4. Build and install openmmg

```bash
git clone https://github.com/ganehag/open-modbusgateway.git ~/open-modbusgateway
cd ~/open-modbusgateway
./configure
make -j$(nproc)
sudo install -m 755 src/openmmg /usr/local/bin/openmmg
```

If `./configure` fails, regenerate it first:

```bash
autoreconf -fi
./configure
make -j$(nproc)
```

### 5. Create the openmmg config

```bash
sudo mkdir -p /etc/openmmg
sudo tee /etc/openmmg/openmmg.conf << 'EOF'
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
EOF
```

Adjust the MQTT host and serial device to match your setup.

### 6. Create the openmmg systemd service

```bash
sudo tee /etc/systemd/system/openmmg.service << 'EOF'
[Unit]
Description=Open MQTT to Modbus Gateway (openmmg)
After=network.target mosquitto.service

[Service]
Type=simple
ExecStart=/usr/local/bin/openmmg -c /etc/openmmg/openmmg.conf
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now openmmg
```

### 7. Set up the web management UI

```bash
sudo mkdir -p /opt/openmmg-web
sudo cp web/app.py /opt/openmmg-web/app.py
python3 -m venv /opt/openmmg-web/venv
/opt/openmmg-web/venv/bin/pip install flask goodwe
```

Create the web UI service:

```bash
sudo tee /etc/systemd/system/openmmg-web.service << 'EOF'
[Unit]
Description=openmmg Web Management UI
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/openmmg-web
ExecStart=/opt/openmmg-web/venv/bin/python /opt/openmmg-web/app.py
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now openmmg-web
```

The web UI is now available at **http://mqtt-bridge.local:8080**.

### 8. Set up the GoodWe MQTT bridge (optional)

If you have a GoodWe solar inverter on the network:

```bash
sudo cp goodwe_mqtt.py /opt/openmmg-web/goodwe_mqtt.py
/opt/openmmg-web/venv/bin/pip install paho-mqtt
```

Create the service:

```bash
sudo tee /etc/systemd/system/goodwe-mqtt.service << 'EOF'
[Unit]
Description=GoodWe Solar Inverter MQTT Bridge
After=network.target mosquitto.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/openmmg-web
ExecStart=/opt/openmmg-web/venv/bin/python /opt/openmmg-web/goodwe_mqtt.py
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now goodwe-mqtt
```

Configure the inverter IP via the web UI (Inverter tab) or edit `/etc/openmmg/goodwe.json` directly.

### 9. Serial port permissions

If using a USB-to-RS485 adapter, add your user to the `dialout` group:

```bash
sudo usermod -a -G dialout chris
```

## Web UI

The web interface at `http://mqtt-bridge.local:8080` provides:

- **Status dashboard** -- openmmg and mosquitto service status, Pi uptime/memory/disk, serial port detection, recent logs
- **Solar inverter dashboard** -- live GoodWe inverter data (PV generation, battery, grid, AC output) with auto-refresh
- **MQTT configuration** -- host, port, topics, authentication, TLS settings
- **Serial gateway management** -- add/remove/configure serial ports (device, baudrate, parity, data/stop bits)
- **Security rules** -- IP filtering rules for TCP Modbus requests
- **Inverter configuration** -- GoodWe inverter IP/port with connection testing
- **Service control** -- save config and restart openmmg with one click
- **Certificate upload** -- drag-and-drop TLS certificates directly through the browser

## TLS / MQTTS

The web UI auto-enables TLS when port 8883 is set. For standard MQTTS brokers, the system CA bundle is selected by default -- no extra configuration needed.

### AWS IoT Core

AWS IoT Core uses mutual TLS (client certificate authentication) instead of username/password. To set it up:

1. Create a Thing in the AWS IoT Console and download the three certificate files
2. Open the web UI and go to the MQTT tab
3. Set **Host** to your endpoint (e.g., `a1b2c3d4e5f6g7.iot.ap-southeast-2.amazonaws.com`)
4. Set **Port** to `8883` (TLS will auto-enable)
5. Upload all three certificate files using the drag-and-drop area:
   - `AmazonRootCA1.pem` -- select this as the **CA Certificate**
   - `<thing>-certificate.pem.crt` -- select this as the **Client Certificate**
   - `<thing>-private.pem.key` -- select this as the **Client Key**
6. Enable **Use client certificate (mutual TLS)**
7. Set **Client ID** to match your IoT Thing name
8. Leave **Username** and **Password** empty
9. Click **Save & Restart**

### Other MQTTS Brokers

For standard MQTTS brokers (HiveMQ, EMQX, Mosquitto with TLS, etc.):

1. Set port to `8883`
2. CA Certificate defaults to the system bundle -- this works for any broker with a publicly signed certificate
3. Set username/password as usual
4. No client certificate needed unless the broker requires mutual TLS

## GoodWe Solar Inverter

The web UI integrates with GoodWe solar inverters via the [goodwe](https://github.com/marcelblijleven/goodwe) Python library. It connects to the inverter's WiFi/LAN dongle over the local network and reads live runtime data.

### Supported Inverters

All GoodWe families: ET, EH, BT, BH, ES, EM, BP, DT, MS, D-NS, XS (and white-label variants).

### Setup

1. Open the web UI and click the **Inverter** config tab
2. Enter the inverter's IP address on your local network
3. Port defaults to **8899** (UDP, standard WiFi dongle). Use **502** for the newer V2.0 LAN+WiFi dongle
4. Click **Test Connection** to verify
5. Live sensor data will appear on the Solar Inverter dashboard card

### Data Displayed

Sensors are grouped by category (varies by inverter model):

- **Solar Panels** -- PV voltage, current, power per string
- **Battery** -- state of charge, voltage, current, power, temperature
- **Grid** -- voltage, frequency, import/export power
- **AC Output** -- output voltage, current, power
- **Backup / UPS** -- backup load data (if applicable)

Data auto-refreshes every 30 seconds.

### MQTT Bridge

The `goodwe-mqtt` daemon bridges inverter register data to MQTT. It reads MQTT broker settings from the openmmg config and inverter settings from `/etc/openmmg/goodwe.json`.

**Request format** (publish to `goodwe/request`):
```
<COOKIE> <INVERTER_ID> <FUNC> <REG> <COUNT> [DATA...]
```

| Field | Description |
|-------|-------------|
| Cookie | Unique ID to match request/response |
| Inverter ID | Matches `id` in inverter config (e.g., `0`, `1`) |
| Func | Modbus function: 3 (read holding), 4 (read input), 6 (write single), 16 (write multi) |
| Reg | Starting register address (e.g., 35100) |
| Count | Number of registers to read |
| Data | Values to write (function 6: one value, function 16: multiple values) |

**Response format** (published to `goodwe/response`):
```
<COOKIE> OK <val1> <val2> ...
<COOKIE> ERR <message>
```

**Auto-polling** reads all inverter register ranges every N seconds (configurable). Poll responses use cookies like `poll_<seq>_<inverter_id>_<start_reg>`.

**Examples:**
```bash
# Subscribe for responses
mosquitto_sub -t "goodwe/response" &

# Read 10 registers from inverter 0 starting at 35100 (PV runtime data)
mosquitto_pub -t "goodwe/request" -m "12345 0 3 35100 10"
# Response: 12345 OK 3200 85 2720 0 3150 82 2583 0 5303 0

# Write value 1 to register 47511 on inverter 1 (set operation mode)
mosquitto_pub -t "goodwe/request" -m "12346 1 6 47511 1"
# Response: 12346 OK
```

### GoodWe Config File

`/etc/openmmg/goodwe.json`:
```json
{
  "inverters": [
    {"id": "0", "name": "Roof East", "host": "192.168.1.100", "port": 8899},
    {"id": "1", "name": "Roof West", "host": "192.168.1.101", "port": 8899}
  ],
  "poll_interval": 30,
  "request_topic": "goodwe/request",
  "response_topic": "goodwe/response"
}
```

### Web API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/goodwe/config` | GET | Current inverter config |
| `/api/goodwe/config` | POST | Save inverter config (restarts goodwe-mqtt) |
| `/api/goodwe/data` | GET | Live runtime data from inverter |

## Configuration Reference

### MQTT Options

| Option | Required | Description |
|--------|----------|-------------|
| `host` | Yes | MQTT broker hostname or IP |
| `port` | Yes | MQTT broker port (default 1883) |
| `request_topic` | Yes | Topic to subscribe for Modbus requests |
| `response_topic` | Yes | Topic to publish Modbus responses |
| `username` | No | MQTT authentication username |
| `password` | No | MQTT authentication password |
| `client_id` | No | MQTT client identifier |
| `keepalive` | No | Keepalive interval in seconds |
| `qos` | No | MQTT QoS level (0, 1, or 2) |
| `retain` | No | Retain flag (true/false) |
| `clean_session` | No | Clean session flag (true/false) |
| `mqtt_protocol` | No | Protocol version (3.1, 3.1.1, 5) |
| `tls_version` | No | TLS version (tlsv1, tlsv1.1, tlsv1.2) |
| `ca_cert_path` | No | Path to CA certificate |
| `cert_path` | No | Path to client certificate |
| `key_path` | No | Path to client private key |
| `verify_ca_cert` | No | Verify CA certificate (true/false) |

### Serial Gateway Options

| Option | Required | Description |
|--------|----------|-------------|
| `id` | Yes | Unique gateway ID (referenced in MQTT requests) |
| `device` | Yes | Serial port path (e.g., `/dev/ttyUSB0`) |
| `baudrate` | No | Baud rate (default 9600) |
| `parity` | No | Parity: none, even, odd (default none) |
| `data_bits` | No | Data bits (default 8) |
| `stop_bits` | No | Stop bits (default 1) |

### Security Rule Options

| Option | Description |
|--------|-------------|
| `ip` | IP address or CIDR range (e.g., `::ffff:127.0.0.1/128`) |
| `port` | Modbus TCP port |
| `slave_id` | Allowed slave ID |
| `function` | Allowed Modbus function code |
| `register_address` | Allowed register range (e.g., `0-65535`) |

## MQTT Protocol

### Serial RTU Request

```
1 <COOKIE> <SERIAL_ID> <TIMEOUT> <SLAVE_ID> <FUNCTION> <REGISTER> <COUNT> [DATA]
```

| Field | Description |
|-------|-------------|
| `1` | Format: serial RTU |
| Cookie | Unique ID to match request/response |
| Serial ID | Matches `id` in serial_gateway config |
| Timeout | Seconds |
| Slave ID | Modbus device address (1-255) |
| Function | Modbus function code (see below) |
| Register | Starting register (1-based) |
| Count | Number of registers/coils |

### TCP Request

```
0 <COOKIE> <IP_TYPE> <IP> <PORT> <TIMEOUT> <SLAVE_ID> <FUNCTION> <REGISTER> <COUNT> [DATA]
```

IP type: `0` = IPv4, `1` = IPv6.

### Function Codes

| Code | Function |
|------|----------|
| 1 | Read coils |
| 2 | Read discrete inputs |
| 3 | Read holding registers |
| 4 | Read input registers |
| 5 | Write single coil |
| 6 | Write single register |
| 15 | Write multiple coils |
| 16 | Write multiple registers |

### Examples

Read 5 holding registers from serial slave 1:
```bash
mosquitto_pub -t "modbus/request" -m "1 99001 0 5 1 3 1 5"
# Response: 99001 OK 100 200 300 400 500
```

Write value 1234 to register 10:
```bash
mosquitto_pub -t "modbus/request" -m "1 99002 0 5 1 6 10 1234"
# Response: 99002 OK
```

## Serial Port Setup

### USB-to-RS485 Adapter

Most USB adapters appear as `/dev/ttyUSB0`:

```bash
ls -la /dev/ttyUSB*
dmesg | grep tty
```

### Built-in UART (GPIO)

To use the Pi's GPIO UART for Modbus:

1. Disable the serial console via `sudo raspi-config` (Interface Options > Serial Port > Login shell: No, Hardware: Yes)
2. For Pi 3/4/5 with Bluetooth, add `dtoverlay=disable-bt` to `/boot/firmware/config.txt` and reboot
3. The UART will be available at `/dev/ttyAMA0`

### RS485 HAT

For SPI-based RS485 HATs (e.g., Waveshare), add the appropriate overlay to `/boot/firmware/config.txt`:

```
dtoverlay=sc16is752-spi1,int_pin=24
```

The port will appear as `/dev/ttySC0`.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `configure: error: libmodbus not found` | `sudo apt-get install libmodbus-dev` |
| `Permission denied: /dev/ttyUSB0` | `sudo usermod -a -G dialout $USER` then re-login |
| No response from Modbus device | Check baudrate, parity, and slave ID match the device |
| MQTT connection refused | Verify mosquitto is running: `sudo systemctl status mosquitto` |
| Web UI not loading | Check service: `sudo systemctl status openmmg-web` |

## Project Files

| File | Description |
|------|-------------|
| `web/app.py` | Flask web management UI (includes GoodWe integration) |
| `goodwe_mqtt.py` | GoodWe inverter MQTT bridge daemon |
| `build_openmmg.sh` | Automated build script for the Pi |
| `modbus_server.py` | Simulated Modbus server for testing |
| `modbus_client.py` | Modbus client for testing |

## License

openmmg is licensed under [GPLv3](https://github.com/ganehag/open-modbusgateway/blob/master/LICENSE). The web UI and scripts in this repository are provided under the same license.
