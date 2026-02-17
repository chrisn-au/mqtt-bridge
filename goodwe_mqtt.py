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
            cfg["host"] = m.get("host", cfg["host"])
            cfg["port"] = str(m.get("port", cfg["port"]))
            if m.get("username"):
                cfg["username"] = m["username"]
            if m.get("password"):
                cfg["password"] = m["password"]
            if m.get("tls"):
                cfg["tls"] = True
            if m.get("ca_cert"):
                cfg["ca_cert"] = m["ca_cert"]
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
        # Per-inverter cache: {inverter_id: [(start_reg, count), ...]}
        self._poll_ranges = {}

    def _get_inverter(self, inverter_id):
        """Look up inverter config by ID."""
        for inv in self.gw_config["inverters"]:
            if str(inv.get("id", "")) == str(inverter_id):
                return inv
        return None

    def setup_mqtt(self):
        """Set up and connect MQTT client."""
        client_id = self.mqtt_config.get("client_id", "")
        client_id = (client_id + "_gw") if client_id else "goodwe_bridge"

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
        """Parse and execute a register read/write request."""
        parts = payload.split()
        if len(parts) < 5:
            cookie = parts[0] if parts else "0"
            return f"{cookie} ERR invalid format: need COOKIE INVERTER_ID FUNC REG COUNT"

        cookie = parts[0]
        inverter_id = parts[1]

        try:
            func = int(parts[2])
            reg = int(parts[3])
            count = int(parts[4])
        except ValueError:
            return f"{cookie} ERR invalid numeric values"

        inv_cfg = self._get_inverter(inverter_id)
        if not inv_cfg:
            return f"{cookie} ERR unknown inverter id '{inverter_id}'"

        host = inv_cfg.get("host", "")
        port = int(inv_cfg.get("port", 8899))
        if not host:
            return f"{cookie} ERR inverter {inverter_id} has no host configured"

        inverter = await goodwe.connect(host, port=port)

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

    async def _read_registers(self, inverter, cookie, offset, count):
        """Read registers and return raw 16-bit values."""
        try:
            cmd = inverter._protocol.read_command(offset, count)
            resp = await cmd.execute(inverter._protocol)
            raw = resp._data.getvalue()
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

    async def _discover_ranges(self, inverter):
        """Determine register ranges to poll from sensor definitions."""
        sensors = inverter.sensors()
        if not sensors:
            return []

        entries = []
        for s in sensors:
            reg_count = max(1, (s.size_ + 1) // 2)
            entries.append((s.offset, s.offset + reg_count - 1))
        entries.sort()

        # Merge into contiguous ranges (allow gap up to 10 registers)
        ranges = []
        start, end = entries[0]
        for s, e in entries[1:]:
            if s - end <= 10:
                end = max(end, e)
            else:
                ranges.append((start, end - start + 1))
                start, end = s, e
        ranges.append((start, end - start + 1))

        # Cap reads at 125 registers (standard Modbus limit)
        final = []
        for s, c in ranges:
            while c > 125:
                final.append((s, 125))
                s += 125
                c -= 125
            if c > 0:
                final.append((s, c))

        return final

    async def _poll_inverter(self, inv_cfg):
        """Poll one inverter's register ranges."""
        inv_id = str(inv_cfg.get("id", "0"))
        host = inv_cfg.get("host", "")
        port = int(inv_cfg.get("port", 8899))
        name = inv_cfg.get("name", host)

        if not host:
            return

        try:
            inverter = await goodwe.connect(host, port=port)

            if inv_id not in self._poll_ranges:
                ranges = await self._discover_ranges(inverter)
                self._poll_ranges[inv_id] = ranges
                if ranges:
                    log.info(
                        "Inverter %s (%s): %d register ranges: %s",
                        inv_id, name, len(ranges),
                        ", ".join(f"{s}-{s + c - 1}" for s, c in ranges),
                    )
                else:
                    log.warning("Inverter %s (%s): no sensor registers found", inv_id, name)
                    return

            for reg_start, count in self._poll_ranges[inv_id]:
                try:
                    cookie = f"poll_{self.poll_seq}_{inv_id}_{reg_start}"
                    result = await self._read_registers(
                        inverter, cookie, reg_start, count
                    )
                    self.client.publish(
                        self.gw_config["response_topic"], result
                    )
                except Exception as e:
                    log.warning(
                        "Inverter %s poll %d-%d failed: %s",
                        inv_id, reg_start, reg_start + count - 1, e,
                    )

        except Exception as e:
            log.error("Inverter %s (%s) poll failed: %s", inv_id, name, e)
            self._poll_ranges.pop(inv_id, None)  # Reset cache on failure

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
