# MQTT Bridge

A Raspberry Pi-based MQTT-to-Modbus gateway using [open-modbusgateway](https://github.com/ganehag/open-modbusgateway) (openmmg). Receives Modbus commands over MQTT, executes them against Modbus TCP or serial RTU targets, and publishes responses back. Includes a web management UI for configuration and monitoring.

```
MQTT Broker                    Raspberry Pi                    Modbus Devices
    │                          ┌────────────┐                       │
    │  modbus/request    ───►  │  openmmg   │ ── /dev/ttyUSB0 ──► Slave 1
    │                          │            │ ── /dev/ttyAMA0 ──► Slave 2
    │  modbus/response   ◄───  │  (C binary)│                       │
    │                          └────────────┘                       │
    │                          │  Web UI    │
    │                          │  :8080     │
    │                          └────────────┘
```

## Pi Setup

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
/opt/openmmg-web/venv/bin/pip install flask
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

### 8. Serial port permissions

If using a USB-to-RS485 adapter, add your user to the `dialout` group:

```bash
sudo usermod -a -G dialout chris
```

## Web UI

The web interface at `http://mqtt-bridge.local:8080` provides:

- **Status dashboard** -- openmmg and mosquitto service status, Pi uptime/memory/disk, serial port detection, recent logs
- **MQTT configuration** -- host, port, topics, authentication, TLS settings
- **Serial gateway management** -- add/remove/configure serial ports (device, baudrate, parity, data/stop bits)
- **Security rules** -- IP filtering rules for TCP Modbus requests
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
| `web/app.py` | Flask web management UI |
| `build_openmmg.sh` | Automated build script for the Pi |
| `modbus_server.py` | Simulated Modbus server for testing |
| `modbus_client.py` | Modbus client for testing |

## License

openmmg is licensed under [GPLv3](https://github.com/ganehag/open-modbusgateway/blob/master/LICENSE). The web UI and scripts in this repository are provided under the same license.
