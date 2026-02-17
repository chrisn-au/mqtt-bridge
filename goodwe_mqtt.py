#!/usr/bin/env python3
"""GoodWe Solar Inverter MQTT Bridge.

Bridges GoodWe inverter data to MQTT, providing:
- Periodic polling of inverter registers (auto-publish)
- On-demand register read/write via MQTT request/response

Request format (on request topic):
    <COOKIE> <FUNC> <REG> <COUNT> [DATA...]

Response format (on response topic):
    <COOKIE> OK <val1> <val2> ...
    <COOKIE> ERR <message>

Auto-poll responses use cookie format: poll_<seq>_<start_reg>
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

GOODWE_CONFIG = "/etc/openmmg/goodwe.json"
OPENMMG_CONFIG = "/etc/openmmg/openmmg.conf"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("goodwe-mqtt")


def load_goodwe_config():
    """Load GoodWe bridge configuration."""
    defaults = {
        "host": "",
        "port": 8899,
        "poll_interval": 30,
        "request_topic": "goodwe/request",
        "response_topic": "goodwe/response",
    }
    try:
        with open(GOODWE_CONFIG) as f:
            cfg = json.load(f)
        defaults.update(cfg)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return defaults


def load_mqtt_config():
    """Load MQTT broker config from openmmg config file."""
    cfg = {"host": "127.0.0.1", "port": "1883"}
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
    """Bridges GoodWe inverter to MQTT."""

    def __init__(self):
        self.running = True
        self.gw_config = load_goodwe_config()
        self.mqtt_config = load_mqtt_config()
        self.client = None
        self.lock = threading.Lock()
        self.poll_seq = 0
        self._poll_ranges = None

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

        tls_version = self.mqtt_config.get("tls_version")
        if tls_version:
            ca = self.mqtt_config.get(
                "ca_cert_path", "/etc/ssl/certs/ca-certificates.crt"
            )
            cert = self.mqtt_config.get("cert_path")
            key = self.mqtt_config.get("key_path")
            self.client.tls_set(
                ca_certs=ca,
                certfile=cert if cert else None,
                keyfile=key if key else None,
            )

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)

        host = self.mqtt_config.get("host", "127.0.0.1")
        port = int(self.mqtt_config.get("port", 1883))
        log.info("Connecting to MQTT broker %s:%d", host, port)
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
        if len(parts) < 4:
            cookie = parts[0] if parts else "0"
            return f"{cookie} ERR invalid format: need COOKIE FUNC REG COUNT"

        cookie = parts[0]
        try:
            func = int(parts[1])
            reg = int(parts[2])
            count = int(parts[3])
        except ValueError:
            return f"{cookie} ERR invalid numeric values"

        host = self.gw_config["host"]
        port = self.gw_config["port"]
        if not host:
            return f"{cookie} ERR inverter not configured"

        inverter = await goodwe.connect(host, port=port)

        if func in (3, 4):
            return await self._read_registers(inverter, cookie, reg, count)
        elif func == 6:
            if len(parts) < 5:
                return f"{cookie} ERR missing write value"
            value = int(parts[4])
            return await self._write_register(inverter, cookie, reg, value)
        elif func == 16:
            if len(parts) < 5:
                return f"{cookie} ERR missing write values"
            data = bytes()
            for v in parts[4:]:
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

    async def _poll(self):
        """Poll all register ranges and publish results."""
        host = self.gw_config["host"]
        port = self.gw_config["port"]
        if not host:
            return

        try:
            inverter = await goodwe.connect(host, port=port)

            if self._poll_ranges is None:
                self._poll_ranges = await self._discover_ranges(inverter)
                if self._poll_ranges:
                    log.info(
                        "Discovered %d register ranges: %s",
                        len(self._poll_ranges),
                        ", ".join(
                            f"{s}-{s + c - 1}" for s, c in self._poll_ranges
                        ),
                    )
                else:
                    log.warning("No sensor registers found")
                    return

            self.poll_seq += 1

            for reg_start, count in self._poll_ranges:
                try:
                    cookie = f"poll_{self.poll_seq}_{reg_start}"
                    result = await self._read_registers(
                        inverter, cookie, reg_start, count
                    )
                    self.client.publish(
                        self.gw_config["response_topic"], result
                    )
                except Exception as e:
                    log.warning(
                        "Poll %d-%d failed: %s",
                        reg_start,
                        reg_start + count - 1,
                        e,
                    )

        except Exception as e:
            log.error("Poll failed: %s", e)
            self._poll_ranges = None  # Reset cache on connection failure

    def run(self):
        """Main run loop."""
        signal.signal(
            signal.SIGTERM, lambda *_: setattr(self, "running", False)
        )
        signal.signal(
            signal.SIGINT, lambda *_: setattr(self, "running", False)
        )

        if not self.gw_config["host"]:
            log.warning(
                "No inverter host configured - set via web UI or %s",
                GOODWE_CONFIG,
            )

        self.setup_mqtt()

        log.info("GoodWe MQTT bridge started")
        log.info(
            "  Inverter: %s:%s",
            self.gw_config["host"] or "(not set)",
            self.gw_config["port"],
        )
        log.info("  Request topic:  %s", self.gw_config["request_topic"])
        log.info("  Response topic: %s", self.gw_config["response_topic"])
        log.info("  Poll interval:  %ds", self.gw_config["poll_interval"])

        interval = max(int(self.gw_config["poll_interval"]), 5)

        while self.running:
            if self.gw_config["host"] and interval > 0:
                with self.lock:
                    try:
                        asyncio.run(self._poll())
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
    GoodWeBridge().run()
