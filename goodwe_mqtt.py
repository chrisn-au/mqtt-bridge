#!/usr/bin/env python3
"""GoodWe Solar Inverter MQTT Bridge.

Bridges one or more GoodWe inverters to MQTT, providing:
- Periodic polling of inverter registers (auto-publish)
- On-demand register read/write via MQTT request/response

Request format (on request topic):
    <COOKIE> <INVERTER_ID> <FUNC> <REG> <COUNT> [DATA...]

Response format (on response topic):
    <COOKIE> OK <val1> <val2> ...
    <COOKIE> ERR <message>

Auto-poll responses use cookie format: poll_<seq>_<inverter_id>_<start_reg>
"""

import asyncio
import json
import logging
import os
import platform
import signal
import struct
import sys
import threading
import time

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("ERROR: paho-mqtt not installed. Run: pip install paho-mqtt", file=sys.stderr)
    sys.exit(1)

try:
    import goodwe
except ImportError:
    print("ERROR: goodwe not installed. Run: pip install goodwe", file=sys.stderr)
    sys.exit(1)

GOODWE_CONFIG = os.environ.get("GOODWE_CONFIG", "/etc/openmmg/goodwe.json")
OPENMMG_CONFIG = os.environ.get("OPENMMG_CONFIG", "/etc/openmmg/openmmg.conf")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("goodwe-mqtt")


def load_goodwe_config():
    """Load GoodWe bridge configuration."""
    defaults = {
        "inverters": [],
        "poll_interval": 30,
        "request_topic": "goodwe/request",
        "response_topic": "goodwe/response",
    }
    try:
        with open(GOODWE_CONFIG) as f:
            cfg = json.load(f)
        # Migrate single-inverter config to multi format
        if "host" in cfg and "inverters" not in cfg:
            if cfg["host"]:
                cfg["inverters"] = [{"id": "0", "host": cfg["host"], "port": cfg.get("port", 8899)}]
            else:
                cfg["inverters"] = []
        defaults.update({k: cfg[k] for k in defaults if k in cfg})
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    # Resolve gateway_id and substitute topic variables
    gateway_id = defaults.get("gateway_id", "") or platform.node().split(".")[0]
    defaults["gateway_id"] = gateway_id
    mqtt_cfg = defaults.get("mqtt", {})
    client_id = mqtt_cfg.get("client_id", "") or gateway_id
    subs = {"gateway_id": gateway_id, "client_id": client_id}
    for key in ("request_topic", "response_topic"):
        try:
            defaults[key] = defaults[key].format(**subs)
        except (KeyError, ValueError):
            pass

    return defaults


def load_mqtt_config():
    """Load MQTT broker config.

    Checks goodwe.json 'mqtt' section first (standalone mode),
    then falls back to openmmg.conf (Pi deployment mode).
    """
    cfg = {"host": "127.0.0.1", "port": "1883"}

    # Try goodwe.json mqtt section first
    try:
        with open(GOODWE_CONFIG) as f:
            gw_cfg = json.load(f)
        if "mqtt" in gw_cfg:
            m = gw_cfg["mqtt"]
            host = m.get("host", cfg["host"])
            # Strip protocol prefixes — MQTT uses raw TCP, not HTTP
            for prefix in ("http://", "https://", "mqtt://", "mqtts://"):
                if host.startswith(prefix):
                    host = host[len(prefix):]
                    break
            cfg["host"] = host.rstrip("/")
            cfg["port"] = str(m.get("port", cfg["port"]))
            if m.get("username"):
                cfg["username"] = m["username"]
            if m.get("password"):
                cfg["password"] = m["password"]
            if m.get("tls"):
                cfg["tls"] = True
            if m.get("ca_cert"):
                cfg["ca_cert"] = m["ca_cert"]
            if m.get("client_id"):
                cfg["client_id"] = m["client_id"]
            return cfg
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass

    # Fall back to openmmg.conf
    try:
        with open(OPENMMG_CONFIG) as f:
            in_mqtt = False
            for line in f:
                line = line.split("#")[0].strip()
                if line.startswith("config mqtt"):
                    in_mqtt = True
                    continue
                elif line.startswith("config "):
                    in_mqtt = False
                    continue
                if in_mqtt and line.startswith("option "):
                    parts = line.split(None, 2)
                    if len(parts) == 3:
                        cfg[parts[1]] = parts[2].strip("'\"")
    except FileNotFoundError:
        pass
    return cfg


class GoodWeBridge:
    """Bridges multiple GoodWe inverters to MQTT."""

    def __init__(self):
        self.running = True
        self.gw_config = load_goodwe_config()
        self.mqtt_config = load_mqtt_config()
        self.client = None
        self.lock = threading.Lock()
        self.poll_seq = 0
        # Cached inverter connections: {(host, port): inverter_object}
        self._inverter_cache = {}

    def _get_inverter(self, inverter_id):
        """Look up inverter config by ID."""
        for inv in self.gw_config["inverters"]:
            if str(inv.get("id", "")) == str(inverter_id):
                return inv
        return None

    async def _connect(self, host, port):
        """Get a cached inverter connection, reconnecting if needed."""
        key = (host, port)
        inv = self._inverter_cache.get(key)
        if inv is None:
            inv = await goodwe.connect(host, port=port)
            self._inverter_cache[key] = inv
            log.info("Connected to inverter at %s:%d (%s)", host, port,
                     getattr(inv, "model_name", "unknown"))
        return inv

    def setup_mqtt(self):
        """Set up and connect MQTT client."""
        client_id = self.mqtt_config.get("client_id", "") or self.gw_config.get("gateway_id", "") or platform.node().split(".")[0]

        try:
            self.client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=client_id,
            )
        except (AttributeError, TypeError):
            self.client = mqtt.Client(client_id=client_id)

        username = self.mqtt_config.get("username")
        password = self.mqtt_config.get("password")
        if username:
            self.client.username_pw_set(username, password or "")

        use_tls = self.mqtt_config.get("tls") or self.mqtt_config.get("tls_version")
        if use_tls:
            ca = (
                self.mqtt_config.get("ca_cert")
                or self.mqtt_config.get("ca_cert_path")
                or None
            )
            cert = self.mqtt_config.get("cert_path")
            key = self.mqtt_config.get("key_path")
            self.client.tls_set(
                ca_certs=ca if ca else None,
                certfile=cert if cert else None,
                keyfile=key if key else None,
            )

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)

        host = self.mqtt_config.get("host", "127.0.0.1")
        port = int(self.mqtt_config.get("port", 1883))
        tls_label = " (TLS)" if use_tls else ""
        log.info("Connecting to MQTT broker %s:%d%s", host, port, tls_label)
        self.client.connect(host, port, keepalive=60)
        self.client.loop_start()

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            topic = self.gw_config["request_topic"]
            client.subscribe(topic)
            log.info("MQTT connected, subscribed to %s", topic)
        else:
            log.error("MQTT connection failed: %s", reason_code)

    def _on_disconnect(self, client, userdata, flags=None, reason_code=None, properties=None):
        if reason_code is not None and reason_code != 0:
            log.warning("MQTT disconnected unexpectedly: %s", reason_code)

    def _on_message(self, client, userdata, msg):
        """Handle incoming MQTT request."""
        payload = msg.payload.decode("utf-8", errors="replace").strip()
        log.info("Request: %s", payload)

        with self.lock:
            try:
                result = asyncio.run(self._handle_request(payload))
            except Exception as e:
                parts = payload.split()
                cookie = parts[0] if parts else "0"
                result = f"{cookie} ERR {e}"
                log.error("Request failed: %s", e)

        self.client.publish(self.gw_config["response_topic"], result)
        log.info("Response: %s", result)

    async def _handle_request(self, payload):
        """Parse and execute a request.

        Formats:
            COOKIE INVERTER_ID FUNC REG COUNT [DATA...]   (register read/write)
            COOKIE INVERTER_ID info                        (device info query)
            COOKIE INVERTER_ID pv|battery|grid|energy|system|all  (sensor query)
            COOKIE INVERTER_ID settings                            (settings query)
        """
        parts = payload.split()
        if len(parts) < 3:
            cookie = parts[0] if parts else "0"
            return f"{cookie} ERR invalid format: need COOKIE INVERTER_ID FUNC ..."

        cookie = parts[0]
        inverter_id = parts[1]

        inv_cfg = self._get_inverter(inverter_id)
        if not inv_cfg:
            return f"{cookie} ERR unknown inverter id '{inverter_id}'"

        host = inv_cfg.get("host", "")
        port = int(inv_cfg.get("port", 8899))
        if not host:
            return f"{cookie} ERR inverter {inverter_id} has no host configured"

        # Handle text commands
        cmd = parts[2].lower()
        if cmd == "info":
            return await self._device_info(host, port, cookie)
        if cmd in ("pv", "battery", "grid", "energy", "system", "all"):
            return await self._query_sensors(host, port, cookie, cmd)
        if cmd == "settings":
            return await self._query_settings(host, port, cookie)

        if len(parts) < 5:
            return f"{cookie} ERR invalid format: need COOKIE INVERTER_ID FUNC REG COUNT"

        try:
            func = int(parts[2])
            reg = int(parts[3])
            count = int(parts[4])
        except ValueError:
            return f"{cookie} ERR invalid numeric values"

        inverter = await self._connect(host, port)

        if func in (3, 4):
            return await self._read_registers(inverter, cookie, reg, count)
        elif func == 6:
            if len(parts) < 6:
                return f"{cookie} ERR missing write value"
            value = int(parts[5])
            return await self._write_register(inverter, cookie, reg, value)
        elif func == 16:
            if len(parts) < 6:
                return f"{cookie} ERR missing write values"
            data = bytes()
            for v in parts[5:]:
                data += int(v).to_bytes(2, "big")
            return await self._write_multi(inverter, cookie, reg, data)
        else:
            return f"{cookie} ERR unsupported function {func}"

    # Sensor groups for read_runtime_data() queries
    SENSOR_GROUPS = {
        "pv": {"vpv1", "ipv1", "ppv1", "pv1_mode",
               "vpv2", "ipv2", "ppv2", "pv2_mode",
               "vpv3", "ipv3", "ppv3", "pv3_mode",
               "vpv4", "ipv4", "ppv4", "pv4_mode"},
        "battery": {"vbattery1", "ibattery1", "pbattery1",
                    "battery_mode", "battery_soc", "battery_soh",
                    "battery_temperature", "battery_status",
                    "battery_charge_limit", "battery_discharge_limit",
                    "battery_error", "battery_warning",
                    "battery_bms", "battery_index",
                    "battery_temperature_bms", "battery_charge_limit_bms",
                    "battery_discharge_limit_bms", "battery_soc_bms"},
        "grid": {"vgrid", "igrid", "pgrid", "fgrid", "grid_mode",
                 "vgrid2", "igrid2", "pgrid2", "fgrid2", "grid_mode2",
                 "vgrid3", "igrid3", "pgrid3", "fgrid3", "grid_mode3",
                 "vload", "iload", "pload", "fload", "load_mode",
                 "apparent_power", "reactive_power", "power_factor",
                 "meter_power", "meter_power2", "meter_power3"},
        "energy": {"e_total", "h_total", "e_day",
                   "e_load_day", "e_load_total",
                   "total_power", "active_power",
                   "e_battery_charge_day", "e_battery_charge_total",
                   "e_battery_discharge_day", "e_battery_discharge_total",
                   "e_grid_export", "e_grid_import",
                   "e_grid_export_day", "e_grid_import_day"},
        "system": {"work_mode", "work_mode_label",
                   "temperature", "temperature2",
                   "error_codes", "warning_code",
                   "safety_country", "safety_country_label",
                   "diagnose_result", "diagnose_result_label",
                   "effective_work_mode"},
    }

    async def _device_info(self, host, port, cookie):
        """Query inverter model, serial number, and family."""
        try:
            inverter = await self._connect(host, port)
            model = getattr(inverter, "model_name", "unknown")
            serial = getattr(inverter, "serial_number", "unknown")
            family = type(inverter).__name__
            return f"{cookie} OK model={model} serial={serial} family={family}"
        except Exception as e:
            return f"{cookie} ERR {e}"

    async def _query_sensors(self, host, port, cookie, group):
        """Query sensor data using read_runtime_data().

        Args:
            group: sensor group name (pv, battery, grid, energy, system, all)
        """
        try:
            inverter = await self._connect(host, port)
            data = await inverter.read_runtime_data()

            if group == "all":
                wanted = None  # Include everything
            else:
                wanted = self.SENSOR_GROUPS.get(group)
                if wanted is None:
                    return f"{cookie} ERR unknown group '{group}'"

            pairs = []
            for key, value in data.items():
                if wanted is not None and key not in wanted:
                    continue
                # Format value — no spaces (space is the pair delimiter)
                if isinstance(value, float):
                    pairs.append(f"{key}={value:.2f}")
                elif isinstance(value, bool):
                    pairs.append(f"{key}={'1' if value else '0'}")
                else:
                    pairs.append(f"{key}={str(value).replace(' ', '_')}")

            return f"{cookie} OK {' '.join(pairs)}"
        except Exception as e:
            return f"{cookie} ERR {e}"

    async def _query_settings(self, host, port, cookie):
        """Query inverter settings using read_settings_data()."""
        try:
            inverter = await self._connect(host, port)
            data = await inverter.read_settings_data()
            pairs = []
            for key, value in data.items():
                if isinstance(value, float):
                    pairs.append(f"{key}={value:.2f}")
                elif isinstance(value, bool):
                    pairs.append(f"{key}={'1' if value else '0'}")
                else:
                    pairs.append(f"{key}={str(value).replace(' ', '_')}")
            return f"{cookie} OK {' '.join(pairs)}"
        except Exception as e:
            return f"{cookie} ERR {e}"

    async def _read_registers(self, inverter, cookie, offset, count):
        """Read registers and return raw 16-bit values."""
        try:
            cmd = inverter._protocol.read_command(offset, count)
            resp = await cmd.execute(inverter._protocol)
            raw = resp.response_data()
            values = []
            for i in range(0, min(count * 2, len(raw)), 2):
                val = struct.unpack(">H", raw[i : i + 2])[0]
                values.append(str(val))
            return f"{cookie} OK {' '.join(values)}"
        except Exception as e:
            return f"{cookie} ERR {e}"

    async def _write_register(self, inverter, cookie, reg, value):
        """Write a single register."""
        try:
            cmd = inverter._protocol.write_command(reg, value)
            await cmd.execute(inverter._protocol)
            return f"{cookie} OK"
        except Exception as e:
            return f"{cookie} ERR {e}"

    async def _write_multi(self, inverter, cookie, offset, values_bytes):
        """Write multiple registers."""
        try:
            cmd = inverter._protocol.write_multi_command(offset, values_bytes)
            await cmd.execute(inverter._protocol)
            return f"{cookie} OK"
        except Exception as e:
            return f"{cookie} ERR {e}"

    async def _poll_inverter(self, inv_cfg):
        """Poll one inverter using read_runtime_data()."""
        inv_id = str(inv_cfg.get("id", "0"))
        host = inv_cfg.get("host", "")
        port = int(inv_cfg.get("port", 8899))
        name = inv_cfg.get("name", host)

        if not host:
            return

        try:
            cookie = f"poll_{self.poll_seq}_{inv_id}"
            result = await self._query_sensors(host, port, cookie, "all")
            self.client.publish(self.gw_config["response_topic"], result)
        except Exception as e:
            log.error("Inverter %s (%s) poll failed: %s", inv_id, name, e)
            self._inverter_cache.pop((host, port), None)

    async def _poll_all(self):
        """Poll all configured inverters."""
        self.poll_seq += 1
        for inv_cfg in self.gw_config["inverters"]:
            await self._poll_inverter(inv_cfg)

    def run(self):
        """Main run loop."""
        signal.signal(
            signal.SIGTERM, lambda *_: setattr(self, "running", False)
        )
        signal.signal(
            signal.SIGINT, lambda *_: setattr(self, "running", False)
        )

        inverters = self.gw_config["inverters"]
        if not inverters:
            log.warning(
                "No inverters configured - set via web UI or %s",
                GOODWE_CONFIG,
            )

        self.setup_mqtt()

        log.info("GoodWe MQTT bridge started")
        log.info("  Inverters: %d configured", len(inverters))
        for inv in inverters:
            log.info(
                "    [%s] %s (%s:%s)",
                inv.get("id", "?"),
                inv.get("name", ""),
                inv.get("host", "?"),
                inv.get("port", 8899),
            )
        log.info("  Request topic:  %s", self.gw_config["request_topic"])
        log.info("  Response topic: %s", self.gw_config["response_topic"])
        log.info("  Poll interval:  %ds", self.gw_config["poll_interval"])

        interval = max(int(self.gw_config["poll_interval"]), 5)

        while self.running:
            if inverters and interval > 0:
                with self.lock:
                    try:
                        asyncio.run(self._poll_all())
                    except Exception as e:
                        log.error("Poll error: %s", e)

            for _ in range(interval):
                if not self.running:
                    break
                time.sleep(1)

        log.info("Shutting down...")
        self.client.loop_stop()
        self.client.disconnect()
        log.info("Done")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="GoodWe Solar Inverter MQTT Bridge")
    parser.add_argument("-c", "--config", help="Path to goodwe.json config file")
    parser.add_argument("--mqtt-host", help="MQTT broker host (overrides openmmg config)")
    parser.add_argument("--mqtt-port", type=int, help="MQTT broker port (overrides openmmg config)")
    args = parser.parse_args()

    if args.config:
        GOODWE_CONFIG = args.config

    bridge = GoodWeBridge()

    if args.mqtt_host:
        bridge.mqtt_config["host"] = args.mqtt_host
    if args.mqtt_port:
        bridge.mqtt_config["port"] = str(args.mqtt_port)

    bridge.run()
