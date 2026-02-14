#!/usr/bin/env python3
"""openmmg Web Management UI - Flask application."""

import os
import re
import subprocess
import json
from flask import Flask, render_template_string, request, jsonify

app = Flask(__name__)

CONFIG_PATH = "/etc/openmmg/openmmg.conf"

# ---------------------------------------------------------------------------
# Config parser / writer
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
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       background: var(--bg); color: var(--text); line-height: 1.5; }
.container { max-width: 960px; margin: 0 auto; padding: 1rem; }
h1 { font-size: 1.5rem; margin-bottom: 1rem; }
h2 { font-size: 1.15rem; margin-bottom: 0.75rem; color: var(--text); }
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
.tabs { display: flex; border-bottom: 2px solid var(--border); margin-bottom: 1rem; }
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
@media (max-width: 600px) {
    .form-row, .form-row-3 { grid-template-columns: 1fr; }
    .grid { grid-template-columns: 1fr 1fr; }
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
            <div><span class="stat-label">Uptime</span><div class="stat-value" id="st-uptime">...</div></div>
            <div><span class="stat-label">Memory</span><div class="stat-value" id="st-memory">...</div></div>
            <div><span class="stat-label">Disk</span><div class="stat-value" id="st-disk">...</div></div>
            <div><span class="stat-label">Serial Ports</span><div class="stat-value" id="st-serial">...</div></div>
        </div>
    </div>

    <!-- Config Tabs -->
    <div class="card">
        <div class="tabs">
            <div class="tab active" data-tab="mqtt">MQTT</div>
            <div class="tab" data-tab="serial">Serial Gateways</div>
            <div class="tab" data-tab="rules">Security Rules</div>
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
            <div class="form-row-3">
                <div><label>MQTT Protocol</label>
                    <select id="mqtt-mqtt_protocol"><option value="">default</option>
                    <option value="3.1">3.1</option><option value="3.1.1">3.1.1</option><option value="5">5</option></select></div>
                <div><label>TLS Version</label>
                    <select id="mqtt-tls_version"><option value="">none</option>
                    <option value="tlsv1">tlsv1</option><option value="tlsv1.1">tlsv1.1</option><option value="tlsv1.2">tlsv1.2</option></select></div>
                <div><label>Verify CA Cert</label>
                    <select id="mqtt-verify_ca_cert"><option value="">default</option>
                    <option value="true">true</option><option value="false">false</option></select></div>
            </div>
            <div class="form-row-3">
                <div><label>CA Cert Path</label><input type="text" id="mqtt-ca_cert_path"></div>
                <div><label>Cert Path</label><input type="text" id="mqtt-cert_path"></div>
                <div><label>Key Path</label><input type="text" id="mqtt-key_path"></div>
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

        <div class="actions" style="margin-top:1rem">
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
        document.getElementById('st-uptime').textContent = data.system.uptime || '—';
        document.getElementById('st-memory').textContent = data.system.memory || '—';
        document.getElementById('st-disk').textContent = data.system.disk || '—';
        document.getElementById('st-serial').textContent = data.serial_ports.length ? data.serial_ports.join(', ') : 'none';
        document.getElementById('log-box').textContent = data.logs || 'No logs available';
    }).catch(() => showToast('Failed to load status', 'error'));
}

// --- Config ---
const MQTT_FIELDS = ['host','port','request_topic','response_topic','username','password',
    'client_id','keepalive','qos','retain','clean_session','mqtt_protocol',
    'tls_version','verify_ca_cert','ca_cert_path','cert_path','key_path'];

function loadConfig() {
    fetch('/api/config').then(r => r.json()).then(data => {
        // MQTT
        MQTT_FIELDS.forEach(f => {
            const el = document.getElementById('mqtt-' + f);
            if (el) el.value = data.mqtt[f] || '';
        });
        // Serial gateways
        renderGateways(data.serial_gateways || []);
        // Rules
        renderRules(data.rules || []);
    }).catch(() => showToast('Failed to load config', 'error'));
}

function collectConfig() {
    const mqtt = {};
    MQTT_FIELDS.forEach(f => {
        const el = document.getElementById('mqtt-' + f);
        if (el && el.value) mqtt[f] = el.value;
    });
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

// --- Init ---
refreshStatus();
loadConfig();
// Auto-refresh status every 30s
setInterval(refreshStatus, 30000);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
