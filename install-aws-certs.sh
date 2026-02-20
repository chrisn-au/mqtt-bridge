#!/bin/bash
# Install AWS IoT Core certificates onto mqtt-bridge
#
# Usage: ./install-aws-certs.sh
#
# Expects a zip file in ~/Downloads/archive/ containing:
#   - A .pem.crt file (device certificate)
#   - A .pem.key file (private key)
#   - A .pem or .txt file (Amazon root CA)

set -e

ZIP_PATH="$HOME/Downloads/archive.zip"
PI_HOST="chris@100.97.134.18"
PI_CERT_DIR="/etc/openmmg/certs"
CLIENT_ID="buildiq_7a204af7_c85f32a0_mlu397qm_z1q7"

echo ""
echo "AWS IoT Core Certificate Installer"
echo "===================================="
echo ""

# ── Check zip file ──
if [ ! -f "$ZIP_PATH" ]; then
    echo "ERROR: $ZIP_PATH not found"
    exit 1
fi
echo "Found zip: $(basename "$ZIP_PATH")"

# ── Extract to temp dir ──
TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

unzip -o "$ZIP_PATH" -d "$TMPDIR" > /dev/null
echo "Extracted $(ls "$TMPDIR" | wc -l | tr -d ' ') files"
echo ""

# ── Identify files ──
CERT=""
KEY=""
CA=""

for f in "$TMPDIR"/*; do
    name=$(basename "$f")
    if echo "$name" | grep -q '\.pem\.crt$'; then
        CERT="$f"
        echo "  Certificate: $name"
    elif echo "$name" | grep -q '\.pem\.key$'; then
        KEY="$f"
        echo "  Private key: $name"
    elif echo "$name" | grep -qi 'root\|amazon\|ca'; then
        CA="$f"
        echo "  Root CA:     $name"
    fi
done

# If CA not found by name, look for remaining .pem or .txt
if [ -z "$CA" ]; then
    for f in "$TMPDIR"/*; do
        [ "$f" = "$CERT" ] && continue
        [ "$f" = "$KEY" ] && continue
        name=$(basename "$f")
        if echo "$name" | grep -qE '\.(pem|txt)$'; then
            CA="$f"
            echo "  Root CA:     $name (by elimination)"
        fi
    done
fi

echo ""

if [ -z "$CERT" ]; then echo "ERROR: Could not identify certificate (.pem.crt)"; exit 1; fi
if [ -z "$KEY" ];  then echo "ERROR: Could not identify private key (.pem.key)"; exit 1; fi
if [ -z "$CA" ];   then echo "ERROR: Could not identify root CA"; exit 1; fi

# ── Verify they look right ──
if ! grep -q "BEGIN CERTIFICATE" "$CERT" 2>/dev/null; then
    echo "WARNING: Certificate file doesn't look like a PEM certificate"
fi
if ! grep -q "BEGIN.*PRIVATE KEY" "$KEY" 2>/dev/null; then
    echo "WARNING: Key file doesn't look like a PEM private key"
fi

# ── Ask for AWS IoT endpoint ──
echo "Enter your AWS IoT endpoint (e.g. a1b2c3d4e5-ats.iot.ap-southeast-2.amazonaws.com)"
echo "  Find it in AWS Console > IoT Core > Settings"
read -p "Endpoint: " AWS_ENDPOINT

if [ -z "$AWS_ENDPOINT" ]; then
    echo "ERROR: Endpoint is required"
    exit 1
fi

echo ""
echo "Summary:"
echo "  Pi:        $PI_HOST"
echo "  Cert dir:  $PI_CERT_DIR"
echo "  Client ID: $CLIENT_ID"
echo "  Endpoint:  $AWS_ENDPOINT"
echo ""
read -p "Proceed? (y/N) " CONFIRM
if [[ ! "$CONFIRM" =~ ^[Yy] ]]; then
    echo "Cancelled."
    exit 0
fi

# ── Create cert dir on Pi ──
echo ""
echo "Creating certificate directory on Pi..."
ssh "$PI_HOST" "sudo mkdir -p $PI_CERT_DIR && sudo chown chris:chris $PI_CERT_DIR"

# ── Copy files ──
echo "Copying certificates..."
scp "$CERT" "$PI_HOST:$PI_CERT_DIR/device-cert.pem"
scp "$KEY"  "$PI_HOST:$PI_CERT_DIR/device-key.pem"
scp "$CA"   "$PI_HOST:$PI_CERT_DIR/root-ca.pem"

# ── Set permissions ──
echo "Setting permissions..."
ssh "$PI_HOST" "sudo chmod 644 $PI_CERT_DIR/device-cert.pem $PI_CERT_DIR/root-ca.pem && sudo chmod 600 $PI_CERT_DIR/device-key.pem"

# ── Verify files on Pi ──
echo ""
echo "Verifying files on Pi..."
ssh "$PI_HOST" "ls -la $PI_CERT_DIR/"

# ── Update goodwe.json ──
echo ""
echo "Updating goodwe.json with AWS IoT config..."
ssh "$PI_HOST" "sudo /opt/openmmg-web/venv/bin/python3 -c \"
import json

with open('/etc/openmmg/goodwe.json') as f:
    cfg = json.load(f)

cfg['mqtt'] = {
    'host': '$AWS_ENDPOINT',
    'port': 8883,
    'tls': True,
    'ca_cert': '$PI_CERT_DIR/root-ca.pem',
    'cert_path': '$PI_CERT_DIR/device-cert.pem',
    'key_path': '$PI_CERT_DIR/device-key.pem',
    'client_id': '$CLIENT_ID',
}

with open('/etc/openmmg/goodwe.json', 'w') as f:
    json.dump(cfg, f, indent=2)

print('Config updated.')
print()
print('mqtt section:')
print(json.dumps(cfg['mqtt'], indent=2))
\""

echo ""
echo "Restarting goodwe-mqtt service..."
ssh "$PI_HOST" "sudo systemctl restart goodwe-mqtt"
sleep 3
ssh "$PI_HOST" "sudo journalctl -u goodwe-mqtt --no-pager -n 10 --since '5 sec ago'"

echo ""
echo "Done! Certificates installed and service restarted."
echo ""
echo "To check status:  ssh $PI_HOST 'sudo systemctl status goodwe-mqtt'"
echo "To check logs:    ssh $PI_HOST 'sudo journalctl -u goodwe-mqtt -f'"
