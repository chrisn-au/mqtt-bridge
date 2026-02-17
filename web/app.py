#!/usr/bin/env python3
"""openmmg Web Management UI - Flask application."""

import os
import re
import subprocess
import json
import asyncio
from flask import Flask, render_template_string, request, jsonify
from werkzeug.utils import secure_filename

try:
    import goodwe
    GOODWE_AVAILABLE = True
except ImportError:
    GOODWE_AVAILABLE = False

app = Flask(__name__)

CONFIG_PATH = "/etc/openmmg/openmmg.conf"
CERTS_DIR = "/etc/openmmg/certs"
GOODWE_CONFIG_PATH = "/etc/openmmg/goodwe.json"

# ---------------------------------------------------------------------------
# Config parser / writer (openmmg)
# ---------------------------------------------------------------------------

def parse_config(path=CONFIG_PATH):
    """Parse openmmg config file into a structured dict."""
    config = {"mqtt": {}, "serial_gateways": [], "rules": []}
    current_section = None
    current_item = None

    try:
        with open(path, "r") as f:
            for line in f:
                line = line.split("#")[0].rstrip("\n")  # strip comments
                stripped = line.strip()

                if stripped.startswith("config mqtt"):
                    current_section = "mqtt"
                    current_item = config["mqtt"]
                    continue
                elif stripped.startswith("config serial_gateway"):
                    current_section = "serial_gateway"
                    current_item = {}
                    config["serial_gateways"].append(current_item)
                    continue
                elif stripped.startswith("config rule"):
                    current_section = "rule"
                    current_item = {}
                    config["rules"].append(current_item)
                    continue
                elif stripped == "":
                    current_section = None
                    current_item = None
                    continue

                if current_item is not None and stripped.startswith("option "):
                    parts = stripped.split(None, 2)
                    if len(parts) == 3:
                        name = parts[1]
                        value = parts[2].strip("'\"")
                        current_item[name] = value
    except FileNotFoundError:
        pass

    return config


def write_config(config, path=CONFIG_PATH):
    """Write a structured dict back to openmmg config format."""
    lines = []

    # MQTT section
    mqtt = config.get("mqtt", {})
    if mqtt:
        lines.append("config mqtt")
        for key, val in mqtt.items():
            if val != "":
                lines.append(f"\toption {key} '{val}'")
        lines.append("")

    # Serial gateways
    for gw in config.get("serial_gateways", []):
        lines.append("config serial_gateway")
        for key, val in gw.items():
            if val != "":
                lines.append(f"\toption {key} '{val}'")
        lines.append("")

    # Security rules
    for rule in config.get("rules", []):
        lines.append("config rule")
        for key, val in rule.items():
            if val != "":
                lines.append(f"\toption {key} '{val}'")
        lines.append("")

    with open(path, "w") as f:
        f.write("\n".join(lines))


def validate_config(config):
    """Validate config before saving. Returns list of error strings."""
    errors = []
    mqtt = config.get("mqtt", {})

    if not mqtt.get("host"):
        errors.append("MQTT host is required")
    if not mqtt.get("port"):
        errors.append("MQTT port is required")
    if not mqtt.get("request_topic"):
        errors.append("MQTT request_topic is required")
    if not mqtt.get("response_topic"):
        errors.append("MQTT response_topic is required")

    host = mqtt.get("host", "")
    if host and "://" in host:
        errors.append("MQTT host must be a bare hostname or IP (no protocol prefix like mqtts://)")

    port = mqtt.get("port", "")
    if port and (not port.isdigit() or not (1 <= int(port) <= 65535)):
        errors.append("MQTT port must be 1-65535")

    qos = mqtt.get("qos", "")
    if qos and qos not in ("0", "1", "2"):
        errors.append("MQTT QoS must be 0, 1, or 2")

    tls = mqtt.get("tls_version", "")
    if tls and tls not in ("tlsv1", "tlsv1.1", "tlsv1.2"):
        errors.append("TLS version must be tlsv1, tlsv1.1, or tlsv1.2")

    for i, gw in enumerate(config.get("serial_gateways", [])):
        if not gw.get("id"):
            errors.append(f"Serial gateway #{i+1}: id is required")
        if not gw.get("device"):
            errors.append(f"Serial gateway #{i+1}: device is required")
        baud = gw.get("baudrate", "9600")
        if baud and not baud.isdigit():
            errors.append(f"Serial gateway #{i+1}: baudrate must be numeric")
        parity = gw.get("parity", "none")
        if parity not in ("none", "even", "odd"):
            errors.append(f"Serial gateway #{i+1}: parity must be none/even/odd")

    return errors


# ---------------------------------------------------------------------------
# GoodWe inverter helpers
# ---------------------------------------------------------------------------

def load_goodwe_config():
    """Load GoodWe inverter configuration."""
    defaults = {
        "host": "", "port": 8899, "poll_interval": 30,
        "request_topic": "goodwe/request", "response_topic": "goodwe/response",
    }
    try:
        with open(GOODWE_CONFIG_PATH) as f:
            defaults.update(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return defaults


def save_goodwe_config(config):
    """Save GoodWe inverter configuration."""
    os.makedirs(os.path.dirname(GOODWE_CONFIG_PATH), exist_ok=True)
    with open(GOODWE_CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


async def _read_goodwe_data(host, port=8899):
    """Connect to GoodWe inverter and read runtime data."""
    inverter = await goodwe.connect(host, port=port)
    runtime = await inverter.read_runtime_data()
    info = {
        "model": getattr(inverter, "model_name", ""),
        "serial": getattr(inverter, "serial_number", ""),
        "firmware": getattr(inverter, "firmware", ""),
        "rated_power": getattr(inverter, "rated_power", 0),
    }
    # Group sensors by kind
    sensors = {}
    for s in inverter.sensors():
        if s.id_ in runtime:
            val = runtime[s.id_]
            if isinstance(val, (bytes, bytearray)):
                continue
            kind = s.kind.name if hasattr(s.kind, "name") else str(s.kind)
            if kind not in sensors:
                sensors[kind] = []
            if hasattr(val, "isoformat"):
                val = val.isoformat()
            elif isinstance(val, float):
                val = round(val, 2)
            sensors[kind].append({
                "id": s.id_,
                "name": s.name,
                "value": val,
                "unit": s.unit,
            })
    return {"info": info, "sensors": sensors}


# ---------------------------------------------------------------------------
# System info helpers
# ---------------------------------------------------------------------------

def run_cmd(cmd, timeout=5):
    """Run a shell command and return stdout."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


def get_service_status(service):
    """Get systemd service status as a dict."""
    active = run_cmd(f"systemctl is-active {service}")
    enabled = run_cmd(f"systemctl is-enabled {service}")
    return {"active": active, "enabled": enabled}


def get_system_info():
    """Get Pi system info."""
    uptime = run_cmd("uptime -p")
    mem = run_cmd("free -h | awk '/^Mem:/{print $3 \"/\" $2}'")
    disk = run_cmd("df -h / | awk 'NR==2{print $3 \"/\" $2 \" (\" $5 \" used)\"}'")
    return {"uptime": uptime, "memory": mem, "disk": disk}


def get_serial_ports():
    """Find available serial ports."""
    ports = []
    for pattern in ["/dev/ttyUSB*", "/dev/ttySC*", "/dev/ttyAMA*", "/dev/serial*"]:
        result = run_cmd(f"ls {pattern} 2>/dev/null")
        if result:
            ports.extend(result.split("\n"))
    return ports


def get_logs(lines=20):
    """Get recent openmmg log entries."""
    return run_cmd(f"journalctl -u openmmg -n {lines} --no-pager 2>/dev/null")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def dashboard():
    return render_template_string(DASHBOARD_HTML)


@app.route("/api/status")
def api_status():
    return jsonify({
        "openmmg": get_service_status("openmmg"),
        "mosquitto": get_service_status("mosquitto"),
        "goodwe_mqtt": get_service_status("goodwe-mqtt"),
        "system": get_system_info(),
        "serial_ports": get_serial_ports(),
        "logs": get_logs(),
    })


@app.route("/api/config")
def api_config():
    return jsonify(parse_config())


@app.route("/api/config", methods=["POST"])
def api_save_config():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    errors = validate_config(data)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400

    try:
        write_config(data)
    except PermissionError:
        return jsonify({"error": "Permission denied writing config file"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    # Restart openmmg to apply changes
    result = subprocess.run(
        ["sudo", "systemctl", "restart", "openmmg"],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        return jsonify({"ok": True, "warning": "Config saved but restart failed: " + result.stderr})

    return jsonify({"ok": True, "message": "Config saved and openmmg restarted"})


@app.route("/api/restart", methods=["POST"])
def api_restart():
    result = subprocess.run(
        ["sudo", "systemctl", "restart", "openmmg"],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        return jsonify({"error": "Restart failed: " + result.stderr}), 500
    return jsonify({"ok": True, "message": "openmmg restarted"})


@app.route("/api/upload-cert", methods=["POST"])
def api_upload_cert():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "No file selected"}), 400
    filename = secure_filename(f.filename)
    if not filename.endswith((".pem", ".crt", ".key", ".cer")):
        return jsonify({"error": "File must be .pem, .crt, .key, or .cer"}), 400
    os.makedirs(CERTS_DIR, exist_ok=True)
    path = os.path.join(CERTS_DIR, filename)
    f.save(path)
    os.chmod(path, 0o600)
    return jsonify({"ok": True, "path": path, "filename": filename})


@app.route("/api/certs")
def api_list_certs():
    """List certificate files already on disk."""
    certs = []
    if os.path.isdir(CERTS_DIR):
        for name in sorted(os.listdir(CERTS_DIR)):
            if name.endswith((".pem", ".crt", ".key", ".cer")):
                certs.append({"name": name, "path": os.path.join(CERTS_DIR, name)})
    return jsonify(certs)


# ---------------------------------------------------------------------------
# GoodWe API routes
# ---------------------------------------------------------------------------

@app.route("/api/goodwe/config")
def api_goodwe_config():
    cfg = load_goodwe_config()
    cfg["available"] = GOODWE_AVAILABLE
    return jsonify(cfg)


@app.route("/api/goodwe/config", methods=["POST"])
def api_save_goodwe_config():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400
    host = (data.get("host") or "").strip()
    port = int(data.get("port", 8899))
    poll_interval = int(data.get("poll_interval", 30))
    config = {
        "host": host,
        "port": port,
        "poll_interval": max(poll_interval, 5) if poll_interval > 0 else 0,
        "request_topic": data.get("request_topic", "goodwe/request").strip(),
        "response_topic": data.get("response_topic", "goodwe/response").strip(),
    }
    try:
        save_goodwe_config(config)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    # Restart goodwe-mqtt if running
    subprocess.run(
        ["sudo", "systemctl", "restart", "goodwe-mqtt"],
        capture_output=True, text=True, timeout=10,
    )
    return jsonify({"ok": True, "message": "Inverter config saved"})


@app.route("/api/goodwe/data")
def api_goodwe_data():
    if not GOODWE_AVAILABLE:
        return jsonify({"error": "goodwe library not installed"}), 500
    cfg = load_goodwe_config()
    if not cfg.get("host"):
        return jsonify({"error": "not_configured"}), 400
    try:
        data = asyncio.run(
            _read_goodwe_data(cfg["host"], cfg.get("port", 8899))
        )
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# HTML Template
# ---------------------------------------------------------------------------

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MQTT Bridge</title>
<style>
:root {
    --bg: #f5f5f5; --card: #fff; --border: #ddd; --text: #333;
    --primary: #2563eb; --primary-hover: #1d4ed8;
    --success: #16a34a; --danger: #dc2626; --warning: #ca8a04;
    --muted: #6b7280; --code-bg: #1e1e1e; --code-fg: #d4d4d4;
    --solar: #f59e0b; --solar-bg: #fffbeb;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       background: var(--bg); color: var(--text); line-height: 1.5; }
.container { max-width: 960px; margin: 0 auto; padding: 1rem; }
h1 { font-size: 1.5rem; margin-bottom: 1rem; }
h2 { font-size: 1.15rem; margin-bottom: 0.75rem; color: var(--text); }
h3 { font-size: 0.95rem; margin-bottom: 0.5rem; color: var(--muted); text-transform: uppercase;
     letter-spacing: 0.05em; border-bottom: 1px solid var(--border); padding-bottom: 0.25rem; }
.card { background: var(--card); border: 1px solid var(--border);
        border-radius: 8px; padding: 1rem; margin-bottom: 1rem; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 0.75rem; }
.stat-label { font-size: 0.8rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }
.stat-value { font-size: 1.1rem; font-weight: 600; }
.badge { display: inline-block; padding: 0.15em 0.5em; border-radius: 4px;
         font-size: 0.8rem; font-weight: 600; color: #fff; }
.badge-active { background: var(--success); }
.badge-inactive { background: var(--danger); }
.badge-unknown { background: var(--muted); }
.log-box { background: var(--code-bg); color: var(--code-fg); padding: 0.75rem;
           border-radius: 6px; font-family: "SF Mono", monospace; font-size: 0.78rem;
           max-height: 300px; overflow-y: auto; white-space: pre-wrap; word-break: break-all; }
.tabs { display: flex; border-bottom: 2px solid var(--border); margin-bottom: 1rem; flex-wrap: wrap; }
.tab { padding: 0.5rem 1rem; cursor: pointer; border-bottom: 2px solid transparent;
       margin-bottom: -2px; color: var(--muted); font-weight: 500; }
.tab.active { color: var(--primary); border-bottom-color: var(--primary); }
.tab-content { display: none; }
.tab-content.active { display: block; }
label { display: block; font-size: 0.85rem; font-weight: 500; margin-bottom: 0.25rem; color: var(--muted); }
input[type="text"], input[type="number"], select {
    width: 100%; padding: 0.5rem; border: 1px solid var(--border);
    border-radius: 6px; font-size: 0.9rem; margin-bottom: 0.75rem; }
input:focus, select:focus { outline: none; border-color: var(--primary); box-shadow: 0 0 0 2px rgba(37,99,235,0.15); }
.form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 0 1rem; }
.form-row-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 0 1rem; }
btn, .btn { display: inline-block; padding: 0.5rem 1rem; border: none; border-radius: 6px;
    font-size: 0.9rem; font-weight: 500; cursor: pointer; text-align: center; }
.btn-primary { background: var(--primary); color: #fff; }
.btn-primary:hover { background: var(--primary-hover); }
.btn-danger { background: var(--danger); color: #fff; }
.btn-danger:hover { background: #b91c1c; }
.btn-sm { padding: 0.3rem 0.7rem; font-size: 0.8rem; }
.btn-outline { background: transparent; border: 1px solid var(--border); color: var(--text); }
.btn-outline:hover { background: var(--bg); }
.btn-solar { background: var(--solar); color: #fff; }
.btn-solar:hover { background: #d97706; }
.actions { display: flex; gap: 0.5rem; margin-top: 0.5rem; }
.toast { position: fixed; bottom: 1.5rem; right: 1.5rem; padding: 0.75rem 1.25rem;
         border-radius: 8px; color: #fff; font-weight: 500; z-index: 999;
         opacity: 0; transition: opacity 0.3s; pointer-events: none; }
.toast.show { opacity: 1; }
.toast-success { background: var(--success); }
.toast-error { background: var(--danger); }
.section-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.75rem; }
.gw-block, .rule-block { border: 1px solid var(--border); border-radius: 6px;
                          padding: 0.75rem; margin-bottom: 0.75rem; position: relative; }
.remove-btn { position: absolute; top: 0.5rem; right: 0.5rem; background: none;
              border: none; color: var(--danger); cursor: pointer; font-size: 1.1rem; font-weight: bold; }
.remove-btn:hover { color: #b91c1c; }
.header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; }
.header h1 { margin-bottom: 0; }
.tls-toggle { margin-bottom: 0.75rem; }
.toggle-label { display: flex; align-items: center; gap: 0.5rem; font-size: 0.9rem; cursor: pointer; }
.toggle-label input[type="checkbox"] { width: 1rem; height: 1rem; cursor: pointer; }
.tls-hint { font-size: 0.8rem; color: var(--muted); font-weight: 400; }
.upload-area { margin-top: 0.75rem; }
.upload-box { border: 2px dashed var(--border); border-radius: 8px; padding: 1rem;
              text-align: center; cursor: pointer; transition: border-color 0.2s, background 0.2s; }
.upload-box.drag-over { border-color: var(--primary); background: rgba(37,99,235,0.05); }
.upload-text { font-size: 0.9rem; color: var(--muted); }
.upload-link { color: var(--primary); cursor: pointer; text-decoration: underline; }
.upload-hint { font-size: 0.75rem; color: var(--muted); margin-top: 0.25rem; }
.cert-item { display: flex; align-items: center; justify-content: space-between;
             padding: 0.4rem 0.6rem; margin-top: 0.5rem; background: var(--bg);
             border-radius: 6px; font-size: 0.85rem; }
.cert-item code { font-size: 0.8rem; color: var(--muted); }

/* Solar card styles */
.solar-card { border-left: 3px solid var(--solar); }
.solar-info { font-size: 0.85rem; color: var(--muted); margin-bottom: 0.75rem; }
.solar-info strong { color: var(--text); }
.solar-group { margin-bottom: 1rem; }
.solar-group:last-child { margin-bottom: 0; }
.solar-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 0.5rem; }
.solar-metric { background: var(--bg); border-radius: 6px; padding: 0.5rem 0.75rem; }
.solar-metric-name { font-size: 0.75rem; color: var(--muted); }
.solar-metric-value { font-size: 1rem; font-weight: 600; }
.solar-metric-unit { font-size: 0.8rem; color: var(--muted); font-weight: 400; }
.solar-placeholder { text-align: center; padding: 2rem 1rem; color: var(--muted); }
.solar-placeholder p { margin-bottom: 0.5rem; }
.solar-error { color: var(--danger); text-align: center; padding: 1rem; }

@media (max-width: 600px) {
    .form-row, .form-row-3 { grid-template-columns: 1fr; }
    .grid { grid-template-columns: 1fr 1fr; }
    .solar-grid { grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); }
}
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>MQTT Bridge</h1>
        <button class="btn btn-danger btn-sm" onclick="restartService()">Restart openmmg</button>
    </div>

    <!-- Status Dashboard -->
    <div class="card" id="status-card">
        <h2>Status</h2>
        <div class="grid" id="status-grid">
            <div><span class="stat-label">openmmg</span><div class="stat-value" id="st-openmmg">...</div></div>
            <div><span class="stat-label">mosquitto</span><div class="stat-value" id="st-mosquitto">...</div></div>
            <div><span class="stat-label">goodwe-mqtt</span><div class="stat-value" id="st-goodwe-mqtt">...</div></div>
            <div><span class="stat-label">Uptime</span><div class="stat-value" id="st-uptime">...</div></div>
            <div><span class="stat-label">Memory</span><div class="stat-value" id="st-memory">...</div></div>
            <div><span class="stat-label">Disk</span><div class="stat-value" id="st-disk">...</div></div>
            <div><span class="stat-label">Serial Ports</span><div class="stat-value" id="st-serial">...</div></div>
        </div>
    </div>

    <!-- Solar Inverter Dashboard -->
    <div class="card solar-card" id="solar-card">
        <div class="section-header">
            <h2>Solar Inverter</h2>
            <button class="btn btn-outline btn-sm" onclick="refreshSolar()">Refresh</button>
        </div>
        <div id="solar-content">
            <div class="solar-placeholder">
                <p>Loading inverter data...</p>
            </div>
        </div>
    </div>

    <!-- Config Tabs -->
    <div class="card">
        <div class="tabs">
            <div class="tab active" data-tab="mqtt">MQTT</div>
            <div class="tab" data-tab="serial">Serial Gateways</div>
            <div class="tab" data-tab="rules">Security Rules</div>
            <div class="tab" data-tab="inverter">Inverter</div>
        </div>

        <!-- MQTT Config -->
        <div class="tab-content active" id="tab-mqtt">
            <h2>MQTT Configuration</h2>
            <div class="form-row">
                <div><label>Host</label><input type="text" id="mqtt-host"></div>
                <div><label>Port</label><input type="text" id="mqtt-port"></div>
            </div>
            <div class="form-row">
                <div><label>Request Topic</label><input type="text" id="mqtt-request_topic"></div>
                <div><label>Response Topic</label><input type="text" id="mqtt-response_topic"></div>
            </div>
            <div class="form-row">
                <div><label>Username</label><input type="text" id="mqtt-username"></div>
                <div><label>Password</label><input type="text" id="mqtt-password"></div>
            </div>
            <div class="form-row">
                <div><label>Client ID</label><input type="text" id="mqtt-client_id"></div>
                <div><label>Keepalive</label><input type="text" id="mqtt-keepalive"></div>
            </div>
            <div class="form-row-3">
                <div><label>QoS</label>
                    <select id="mqtt-qos"><option value="">default</option>
                    <option value="0">0</option><option value="1">1</option><option value="2">2</option></select></div>
                <div><label>Retain</label>
                    <select id="mqtt-retain"><option value="">default</option>
                    <option value="true">true</option><option value="false">false</option></select></div>
                <div><label>Clean Session</label>
                    <select id="mqtt-clean_session"><option value="">default</option>
                    <option value="true">true</option><option value="false">false</option></select></div>
            </div>
            <div class="form-row">
                <div><label>MQTT Protocol</label>
                    <select id="mqtt-mqtt_protocol"><option value="">default (3.1.1)</option>
                    <option value="3.1">3.1</option><option value="3.1.1">3.1.1</option><option value="5">5</option></select></div>
                <div></div>
            </div>

            <!-- TLS Section -->
            <div class="tls-toggle">
                <label class="toggle-label">
                    <input type="checkbox" id="tls-enabled"> <strong>Enable TLS</strong>
                    <span class="tls-hint" id="tls-hint"></span>
                </label>
            </div>
            <div id="tls-options" style="display:none">
                <div class="form-row-3">
                    <div><label>TLS Version</label>
                        <select id="mqtt-tls_version">
                        <option value="tlsv1.2">TLS 1.2 (recommended)</option>
                        <option value="tlsv1.1">TLS 1.1</option>
                        <option value="tlsv1">TLS 1.0</option></select></div>
                    <div><label>CA Certificate</label>
                        <select id="mqtt-ca_cert_path">
                        <option value="/etc/ssl/certs/ca-certificates.crt">System CA bundle (default)</option>
                        </select></div>
                    <div><label>Verify Server Cert</label>
                        <select id="mqtt-verify_ca_cert"><option value="">yes (default)</option>
                        <option value="false">no (insecure)</option></select></div>
                </div>
                <div class="tls-toggle" style="margin-top:0.25rem">
                    <label class="toggle-label">
                        <input type="checkbox" id="client-cert-enabled"> Use client certificate (mutual TLS)
                    </label>
                </div>
                <div id="client-cert-options" style="display:none">
                    <div class="form-row">
                        <div><label>Client Certificate</label>
                            <select id="mqtt-cert_path"><option value="">-- select --</option></select></div>
                        <div><label>Client Key</label>
                            <select id="mqtt-key_path"><option value="">-- select --</option></select></div>
                    </div>
                </div>
                <div class="upload-area" id="cert-upload-area">
                    <div class="upload-box" id="upload-box">
                        <div class="upload-text">Drop certificate files here or <label class="upload-link" for="cert-file-input">browse</label></div>
                        <div class="upload-hint">.pem, .crt, .key, .cer files</div>
                        <input type="file" id="cert-file-input" multiple accept=".pem,.crt,.key,.cer" style="display:none">
                    </div>
                    <div id="cert-list"></div>
                </div>
            </div>
        </div>

        <!-- Serial Gateways -->
        <div class="tab-content" id="tab-serial">
            <div class="section-header">
                <h2>Serial Gateways</h2>
                <button class="btn btn-outline btn-sm" onclick="addGateway()">+ Add Gateway</button>
            </div>
            <div id="gateways-list"></div>
        </div>

        <!-- Security Rules -->
        <div class="tab-content" id="tab-rules">
            <div class="section-header">
                <h2>Security Rules</h2>
                <button class="btn btn-outline btn-sm" onclick="addRule()">+ Add Rule</button>
            </div>
            <div id="rules-list"></div>
        </div>

        <!-- Inverter Config -->
        <div class="tab-content" id="tab-inverter">
            <h2>GoodWe Inverter</h2>
            <p style="font-size:0.85rem;color:var(--muted);margin-bottom:1rem">
                Connect to a GoodWe solar inverter on your local network via its WiFi/LAN dongle.
                The MQTT bridge daemon publishes register data to MQTT topics.
            </p>
            <div class="form-row">
                <div><label>Inverter IP Address</label><input type="text" id="gw-host" placeholder="e.g. 192.168.1.100"></div>
                <div><label>Port</label><input type="text" id="gw-port" value="8899" placeholder="8899"></div>
            </div>
            <h3 style="margin-top:1rem;margin-bottom:0.5rem;font-size:0.9rem;color:var(--muted)">MQTT Bridge</h3>
            <div class="form-row">
                <div><label>Request Topic</label><input type="text" id="gw-request-topic" value="goodwe/request"></div>
                <div><label>Response Topic</label><input type="text" id="gw-response-topic" value="goodwe/response"></div>
            </div>
            <div class="form-row">
                <div><label>Poll Interval (seconds)</label><input type="text" id="gw-poll-interval" value="30" placeholder="30"></div>
                <div></div>
            </div>
            <div class="actions">
                <button class="btn btn-solar" onclick="saveInverterConfig()">Save Inverter Config</button>
                <button class="btn btn-outline" onclick="testInverter()">Test Connection</button>
            </div>
            <div id="inverter-test-result" style="margin-top:0.75rem"></div>
        </div>

        <div class="actions" style="margin-top:1rem" id="main-save-actions">
            <button class="btn btn-primary" onclick="saveConfig()">Save &amp; Restart</button>
        </div>
    </div>

    <!-- Logs -->
    <div class="card">
        <div class="section-header">
            <h2>Recent Logs</h2>
            <button class="btn btn-outline btn-sm" onclick="refreshStatus()">Refresh</button>
        </div>
        <div class="log-box" id="log-box">Loading...</div>
    </div>
</div>

<div class="toast" id="toast"></div>

<script>
// --- Tabs ---
document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        tab.classList.add('active');
        document.getElementById('tab-' + tab.dataset.tab).classList.add('active');
        // Hide main save button on inverter tab (it has its own save)
        document.getElementById('main-save-actions').style.display =
            tab.dataset.tab === 'inverter' ? 'none' : 'flex';
    });
});

// --- Toast ---
function showToast(msg, type) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.className = 'toast toast-' + type + ' show';
    setTimeout(() => t.classList.remove('show'), 3000);
}

// --- Status ---
function badge(state) {
    const cls = state === 'active' ? 'badge-active' : (state === 'inactive' || state === 'failed') ? 'badge-inactive' : 'badge-unknown';
    return '<span class="badge ' + cls + '">' + state + '</span>';
}

function refreshStatus() {
    fetch('/api/status').then(r => r.json()).then(data => {
        document.getElementById('st-openmmg').innerHTML = badge(data.openmmg.active);
        document.getElementById('st-mosquitto').innerHTML = badge(data.mosquitto.active);
        document.getElementById('st-goodwe-mqtt').innerHTML = badge(data.goodwe_mqtt.active);
        document.getElementById('st-uptime').textContent = data.system.uptime || '\u2014';
        document.getElementById('st-memory').textContent = data.system.memory || '\u2014';
        document.getElementById('st-disk').textContent = data.system.disk || '\u2014';
        document.getElementById('st-serial').textContent = data.serial_ports.length ? data.serial_ports.join(', ') : 'none';
        document.getElementById('log-box').textContent = data.logs || 'No logs available';
    }).catch(() => showToast('Failed to load status', 'error'));
}

// --- TLS UI ---
const tlsToggle = document.getElementById('tls-enabled');
const tlsOptions = document.getElementById('tls-options');
const clientCertToggle = document.getElementById('client-cert-enabled');
const clientCertOptions = document.getElementById('client-cert-options');
const caSelect = document.getElementById('mqtt-ca_cert_path');
const certSelect = document.getElementById('mqtt-cert_path');
const keySelect = document.getElementById('mqtt-key_path');
const portField = document.getElementById('mqtt-port');
const tlsHint = document.getElementById('tls-hint');
const uploadBox = document.getElementById('upload-box');
const certFileInput = document.getElementById('cert-file-input');

tlsToggle.addEventListener('change', () => {
    tlsOptions.style.display = tlsToggle.checked ? 'block' : 'none';
});
clientCertToggle.addEventListener('change', () => {
    clientCertOptions.style.display = clientCertToggle.checked ? 'block' : 'none';
});
portField.addEventListener('input', () => {
    const port = portField.value.trim();
    if (port === '8883' && !tlsToggle.checked) {
        tlsHint.textContent = '(port 8883 typically requires TLS)';
        tlsToggle.checked = true;
        tlsOptions.style.display = 'block';
    } else if (port === '1883' && tlsToggle.checked) {
        tlsHint.textContent = '(port 1883 is typically plain MQTT)';
    } else {
        tlsHint.textContent = '';
    }
});

// --- Cert upload ---
uploadBox.addEventListener('click', () => certFileInput.click());
uploadBox.addEventListener('dragover', e => { e.preventDefault(); uploadBox.classList.add('drag-over'); });
uploadBox.addEventListener('dragleave', () => uploadBox.classList.remove('drag-over'));
uploadBox.addEventListener('drop', e => {
    e.preventDefault();
    uploadBox.classList.remove('drag-over');
    uploadFiles(e.dataTransfer.files);
});
certFileInput.addEventListener('change', () => uploadFiles(certFileInput.files));

function uploadFiles(files) {
    Array.from(files).forEach(file => {
        const form = new FormData();
        form.append('file', file);
        fetch('/api/upload-cert', { method: 'POST', body: form })
            .then(r => r.json())
            .then(data => {
                if (data.ok) {
                    showToast('Uploaded ' + data.filename, 'success');
                    refreshCerts();
                } else {
                    showToast(data.error, 'error');
                }
            }).catch(() => showToast('Upload failed', 'error'));
    });
}

function refreshCerts(thenSelectCa, thenSelectCert, thenSelectKey) {
    fetch('/api/certs').then(r => r.json()).then(certs => {
        // Update CA dropdown (keep system bundle as first option)
        const caVal = thenSelectCa || caSelect.value;
        caSelect.innerHTML = '<option value="/etc/ssl/certs/ca-certificates.crt">System CA bundle (default)</option>';
        certs.forEach(c => {
            if (c.name.endsWith('.key')) return; // keys aren't CA certs
            caSelect.insertAdjacentHTML('beforeend',
                '<option value="' + c.path + '">' + c.name + '</option>');
        });
        if (caVal) caSelect.value = caVal;

        // Update cert/key dropdowns
        const certVal = thenSelectCert || certSelect.value;
        const keyVal = thenSelectKey || keySelect.value;
        certSelect.innerHTML = '<option value="">-- select --</option>';
        keySelect.innerHTML = '<option value="">-- select --</option>';
        certs.forEach(c => {
            if (c.name.endsWith('.key')) {
                keySelect.insertAdjacentHTML('beforeend',
                    '<option value="' + c.path + '">' + c.name + '</option>');
            } else {
                certSelect.insertAdjacentHTML('beforeend',
                    '<option value="' + c.path + '">' + c.name + '</option>');
                // Also add to key in case naming is non-standard
            }
        });
        if (certVal) certSelect.value = certVal;
        if (keyVal) keySelect.value = keyVal;

        // Show uploaded files list
        const listEl = document.getElementById('cert-list');
        if (certs.length) {
            listEl.innerHTML = certs.map(c =>
                '<div class="cert-item"><span>' + c.name + '</span><code>' + c.path + '</code></div>'
            ).join('');
        } else {
            listEl.innerHTML = '';
        }
    });
}

// --- Config ---
const MQTT_SIMPLE_FIELDS = ['host','port','request_topic','response_topic','username','password',
    'client_id','keepalive','qos','retain','clean_session','mqtt_protocol'];

function loadConfig() {
    fetch('/api/config').then(r => r.json()).then(data => {
        // Simple MQTT fields
        MQTT_SIMPLE_FIELDS.forEach(f => {
            const el = document.getElementById('mqtt-' + f);
            if (el) el.value = data.mqtt[f] || '';
        });
        // TLS fields
        const hasTls = data.mqtt.tls_version || data.mqtt.ca_cert_path;
        tlsToggle.checked = !!hasTls;
        tlsOptions.style.display = hasTls ? 'block' : 'none';
        if (data.mqtt.tls_version) {
            document.getElementById('mqtt-tls_version').value = data.mqtt.tls_version;
        }
        if (data.mqtt.verify_ca_cert) {
            document.getElementById('mqtt-verify_ca_cert').value = data.mqtt.verify_ca_cert;
        }
        // Client cert
        const hasClientCert = data.mqtt.cert_path || data.mqtt.key_path;
        clientCertToggle.checked = !!hasClientCert;
        clientCertOptions.style.display = hasClientCert ? 'block' : 'none';
        // Load cert files then set selected values
        refreshCerts(data.mqtt.ca_cert_path, data.mqtt.cert_path, data.mqtt.key_path);
        // Serial gateways
        renderGateways(data.serial_gateways || []);
        // Rules
        renderRules(data.rules || []);
    }).catch(() => showToast('Failed to load config', 'error'));
}

function collectConfig() {
    const mqtt = {};
    MQTT_SIMPLE_FIELDS.forEach(f => {
        const el = document.getElementById('mqtt-' + f);
        if (el && el.value) mqtt[f] = el.value;
    });
    // TLS
    if (tlsToggle.checked) {
        mqtt.tls_version = document.getElementById('mqtt-tls_version').value;
        mqtt.ca_cert_path = caSelect.value;
        const verify = document.getElementById('mqtt-verify_ca_cert').value;
        if (verify) mqtt.verify_ca_cert = verify;
        if (clientCertToggle.checked) {
            const cert = certSelect.value;
            const key = keySelect.value;
            if (cert) mqtt.cert_path = cert;
            if (key) mqtt.key_path = key;
        }
    }
    return {
        mqtt: mqtt,
        serial_gateways: collectGateways(),
        rules: collectRules()
    };
}

function saveConfig() {
    const cfg = collectConfig();
    fetch('/api/config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(cfg)
    }).then(r => r.json().then(d => ({ok: r.ok, data: d}))).then(({ok, data}) => {
        if (ok && data.ok) {
            showToast(data.message || 'Saved', 'success');
            setTimeout(refreshStatus, 2000);
        } else {
            const msg = data.details ? data.details.join(', ') : data.error;
            showToast('Error: ' + msg, 'error');
        }
    }).catch(() => showToast('Save failed', 'error'));
}

function restartService() {
    if (!confirm('Restart openmmg service?')) return;
    fetch('/api/restart', {method:'POST'}).then(r => r.json()).then(data => {
        showToast(data.ok ? 'Restarted' : data.error, data.ok ? 'success' : 'error');
        setTimeout(refreshStatus, 2000);
    }).catch(() => showToast('Restart failed', 'error'));
}

// --- Serial Gateways ---
let gwCounter = 0;
function gwHtml(gw, idx) {
    return `<div class="gw-block" data-gw="${idx}">
        <button class="remove-btn" onclick="this.parentElement.remove()">&times;</button>
        <div class="form-row-3">
            <div><label>ID</label><input type="text" class="gw-id" value="${gw.id || ''}"></div>
            <div><label>Device</label><input type="text" class="gw-device" value="${gw.device || ''}"></div>
            <div><label>Baudrate</label><input type="text" class="gw-baudrate" value="${gw.baudrate || '9600'}"></div>
        </div>
        <div class="form-row-3">
            <div><label>Parity</label>
                <select class="gw-parity">
                    <option value="none" ${gw.parity==='none'||!gw.parity?'selected':''}>none</option>
                    <option value="even" ${gw.parity==='even'?'selected':''}>even</option>
                    <option value="odd" ${gw.parity==='odd'?'selected':''}>odd</option>
                </select></div>
            <div><label>Data Bits</label><input type="text" class="gw-data_bits" value="${gw.data_bits || '8'}"></div>
            <div><label>Stop Bits</label><input type="text" class="gw-stop_bits" value="${gw.stop_bits || '1'}"></div>
        </div>
        <div class="form-row">
            <div><label>Slave ID</label><input type="text" class="gw-slave_id" value="${gw.slave_id || ''}"></div>
            <div><label>IP (TCP bridge)</label><input type="text" class="gw-ip" value="${gw.ip || ''}"></div>
        </div>
        <div class="form-row">
            <div><label>Port (TCP bridge)</label><input type="text" class="gw-port" value="${gw.port || ''}"></div>
            <div></div>
        </div>
    </div>`;
}

function renderGateways(gws) {
    document.getElementById('gateways-list').innerHTML = gws.map((g, i) => gwHtml(g, i)).join('');
}

function addGateway() {
    document.getElementById('gateways-list').insertAdjacentHTML('beforeend',
        gwHtml({id: String(document.querySelectorAll('.gw-block').length)}, gwCounter++));
}

function collectGateways() {
    const gws = [];
    document.querySelectorAll('.gw-block').forEach(el => {
        const gw = {};
        const fields = ['id','device','baudrate','parity','data_bits','stop_bits','slave_id','ip','port'];
        fields.forEach(f => {
            const inp = el.querySelector('.gw-' + f);
            if (inp && inp.value) gw[f] = inp.value;
        });
        gws.push(gw);
    });
    return gws;
}

// --- Security Rules ---
let ruleCounter = 0;
function ruleHtml(rule, idx) {
    return `<div class="rule-block" data-rule="${idx}">
        <button class="remove-btn" onclick="this.parentElement.remove()">&times;</button>
        <div class="form-row-3">
            <div><label>IP (CIDR)</label><input type="text" class="rule-ip" value="${rule.ip || ''}"></div>
            <div><label>Port</label><input type="text" class="rule-port" value="${rule.port || ''}"></div>
            <div><label>Slave ID</label><input type="text" class="rule-slave_id" value="${rule.slave_id || ''}"></div>
        </div>
        <div class="form-row">
            <div><label>Function</label><input type="text" class="rule-function" value="${rule.function || ''}"></div>
            <div><label>Register Address</label><input type="text" class="rule-register_address" value="${rule.register_address || ''}"></div>
        </div>
    </div>`;
}

function renderRules(rules) {
    document.getElementById('rules-list').innerHTML = rules.map((r, i) => ruleHtml(r, i)).join('');
}

function addRule() {
    document.getElementById('rules-list').insertAdjacentHTML('beforeend',
        ruleHtml({}, ruleCounter++));
}

function collectRules() {
    const rules = [];
    document.querySelectorAll('.rule-block').forEach(el => {
        const rule = {};
        ['ip','port','slave_id','function','register_address'].forEach(f => {
            const inp = el.querySelector('.rule-' + f);
            if (inp && inp.value) rule[f] = inp.value;
        });
        rules.push(rule);
    });
    return rules;
}

// --- GoodWe Solar Inverter ---
const KIND_LABELS = {
    'PV': 'Solar Panels',
    'BAT': 'Battery',
    'GRID': 'Grid',
    'AC': 'AC Output',
    'UPS': 'Backup / UPS',
    'BMS': 'Battery Management',
};
const KIND_ORDER = ['PV', 'BAT', 'GRID', 'AC', 'UPS', 'BMS'];

function loadInverterConfig() {
    fetch('/api/goodwe/config').then(r => r.json()).then(data => {
        document.getElementById('gw-host').value = data.host || '';
        document.getElementById('gw-port').value = data.port || 8899;
        document.getElementById('gw-request-topic').value = data.request_topic || 'goodwe/request';
        document.getElementById('gw-response-topic').value = data.response_topic || 'goodwe/response';
        document.getElementById('gw-poll-interval').value = data.poll_interval || 30;
    }).catch(() => {});
}

function saveInverterConfig() {
    const host = document.getElementById('gw-host').value.trim();
    const port = document.getElementById('gw-port').value.trim() || '8899';
    const reqTopic = document.getElementById('gw-request-topic').value.trim() || 'goodwe/request';
    const resTopic = document.getElementById('gw-response-topic').value.trim() || 'goodwe/response';
    const pollInterval = document.getElementById('gw-poll-interval').value.trim() || '30';
    fetch('/api/goodwe/config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            host: host, port: parseInt(port),
            request_topic: reqTopic, response_topic: resTopic,
            poll_interval: parseInt(pollInterval)
        })
    }).then(r => r.json()).then(data => {
        if (data.ok) {
            showToast('Inverter config saved', 'success');
            refreshSolar();
        } else {
            showToast('Error: ' + data.error, 'error');
        }
    }).catch(() => showToast('Save failed', 'error'));
}

function testInverter() {
    const resultEl = document.getElementById('inverter-test-result');
    resultEl.innerHTML = '<span style="color:var(--muted)">Connecting...</span>';
    // Save first, then test
    const host = document.getElementById('gw-host').value.trim();
    const port = document.getElementById('gw-port').value.trim() || '8899';
    if (!host) {
        resultEl.innerHTML = '<span style="color:var(--danger)">Enter an IP address first</span>';
        return;
    }
    fetch('/api/goodwe/config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({host: host, port: parseInt(port)})
    }).then(() => fetch('/api/goodwe/data')).then(r => r.json()).then(data => {
        if (data.error) {
            resultEl.innerHTML = '<span style="color:var(--danger)">Connection failed: ' +
                data.error + '</span>';
        } else {
            const info = data.info || {};
            resultEl.innerHTML = '<span style="color:var(--success)">Connected! ' +
                (info.model || 'Unknown model') + ' (Serial: ' + (info.serial || 'N/A') + ')</span>';
            refreshSolar();
        }
    }).catch(() => {
        resultEl.innerHTML = '<span style="color:var(--danger)">Connection failed (timeout or network error)</span>';
    });
}

function refreshSolar() {
    const el = document.getElementById('solar-content');
    fetch('/api/goodwe/data').then(r => r.json()).then(data => {
        if (data.error === 'not_configured') {
            el.innerHTML = '<div class="solar-placeholder">' +
                '<p>No inverter configured</p>' +
                '<p style="font-size:0.85rem">Set the inverter IP address in the <a href="#" onclick="' +
                "document.querySelector('[data-tab=inverter]').click();return false;" +
                '" style="color:var(--primary)">Inverter</a> config tab below.</p></div>';
            return;
        }
        if (data.error) {
            el.innerHTML = '<div class="solar-error">Unable to reach inverter: ' +
                data.error + '</div>';
            return;
        }
        renderSolarData(data);
    }).catch(() => {
        el.innerHTML = '<div class="solar-error">Failed to fetch inverter data</div>';
    });
}

function renderSolarData(data) {
    const el = document.getElementById('solar-content');
    let html = '';

    // Info bar
    const info = data.info || {};
    if (info.model || info.serial) {
        html += '<div class="solar-info">';
        if (info.model) html += '<strong>' + info.model + '</strong>';
        if (info.serial) html += ' &middot; Serial: ' + info.serial;
        if (info.firmware) html += ' &middot; FW: ' + info.firmware;
        if (info.rated_power) html += ' &middot; ' + (info.rated_power / 1000).toFixed(1) + ' kW';
        html += '</div>';
    }

    // Sensor groups
    const sensors = data.sensors || {};
    const sortedKinds = KIND_ORDER.filter(k => sensors[k]);
    // Add any kinds not in our predefined order
    Object.keys(sensors).forEach(k => { if (!sortedKinds.includes(k)) sortedKinds.push(k); });

    sortedKinds.forEach(kind => {
        const items = sensors[kind];
        if (!items || !items.length) return;
        const label = KIND_LABELS[kind] || kind;
        html += '<div class="solar-group"><h3>' + label + '</h3><div class="solar-grid">';
        items.forEach(s => {
            let displayVal = s.value;
            if (typeof displayVal === 'number') {
                // Format large numbers with commas
                if (Math.abs(displayVal) >= 1000) {
                    displayVal = displayVal.toLocaleString();
                }
            }
            const unit = s.unit || '';
            html += '<div class="solar-metric">' +
                '<div class="solar-metric-name">' + s.name + '</div>' +
                '<div class="solar-metric-value">' + displayVal +
                (unit ? ' <span class="solar-metric-unit">' + unit + '</span>' : '') +
                '</div></div>';
        });
        html += '</div></div>';
    });

    if (!html) {
        html = '<div class="solar-placeholder"><p>No sensor data available</p></div>';
    }
    el.innerHTML = html;
}

// --- Init ---
refreshStatus();
loadConfig();
loadInverterConfig();
refreshSolar();
// Auto-refresh every 30s
setInterval(refreshStatus, 30000);
setInterval(refreshSolar, 30000);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
