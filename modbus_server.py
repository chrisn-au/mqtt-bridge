#!/usr/bin/env python3
"""
Simulated Modbus TCP server (slave device) for testing openmmg.

Exposes holding registers, input registers, coils, and discrete inputs
with sample data. Listens on port 5020 by default.

Usage:
    source .venv/bin/activate
    python modbus_server.py [--port 5020] [--slave-id 1]

Then point openmmg at it via MQTT:
    mosquitto_pub -t "modbus/request" -m "0 12345 4 127.0.0.1 5020 5 1 3 0 10"
"""

import argparse
import signal
import sys

from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusDeviceContext,
    ModbusServerContext,
)
from pymodbus.server import StartTcpServer


def build_datastore():
    """Create a slave context with sample data."""

    # Holding registers (func 3/6/16) - addresses 0-99
    # Simulate some sensor readings
    holding = [0] * 100
    holding[0] = 100    # Temperature (x10, so 10.0°C)
    holding[1] = 550    # Humidity (x10, so 55.0%)
    holding[2] = 1013   # Pressure (hPa)
    holding[3] = 2400   # Voltage (x10, so 240.0V)
    holding[4] = 150    # Current (x100, so 1.50A)
    holding[5] = 3600   # Power (W)
    holding[6] = 50     # Frequency (x10, so 5.0Hz... or 50 Hz)
    holding[7] = 1234   # Serial number part 1
    holding[8] = 5678   # Serial number part 2
    holding[9] = 1      # Device status (1=OK)

    # Input registers (func 4) - addresses 0-99
    # Read-only sensor data
    inputs = [0] * 100
    inputs[0] = 251     # Temperature (x10, so 25.1°C)
    inputs[1] = 623     # Humidity (x10, so 62.3%)
    inputs[2] = 1015    # Pressure
    inputs[3] = 4096    # ADC reading
    inputs[4] = 42      # Counter

    # Coils (func 1/5/15) - addresses 0-31
    # On/off switches
    coils = [False] * 32
    coils[0] = True     # Relay 1: ON
    coils[1] = False    # Relay 2: OFF
    coils[2] = True     # Relay 3: ON
    coils[3] = False    # Relay 4: OFF

    # Discrete inputs (func 2) - addresses 0-31
    # Read-only on/off status
    discretes = [False] * 32
    discretes[0] = True   # Sensor 1: active
    discretes[1] = True   # Sensor 2: active
    discretes[2] = False  # Sensor 3: inactive
    discretes[3] = True   # Sensor 4: active

    store = ModbusDeviceContext(
        di=ModbusSequentialDataBlock(0, [int(v) for v in discretes]),
        co=ModbusSequentialDataBlock(0, [int(v) for v in coils]),
        hr=ModbusSequentialDataBlock(0, holding),
        ir=ModbusSequentialDataBlock(0, inputs),
    )
    return store


def main():
    parser = argparse.ArgumentParser(description="Simulated Modbus TCP server")
    parser.add_argument("--port", type=int, default=5020, help="TCP port (default: 5020)")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--slave-id", type=int, default=1, help="Slave/unit ID (default: 1)")
    args = parser.parse_args()

    store = build_datastore()
    # Map the slave ID to our datastore
    context = ModbusServerContext(devices={args.slave_id: store}, single=False)

    def shutdown(sig, frame):
        print("\nShutting down...")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print(f"Modbus TCP server starting on {args.host}:{args.port}")
    print(f"Slave ID: {args.slave_id}")
    print()
    print("Sample data loaded:")
    print("  Holding registers (func 3): 0-9 have sensor values")
    print("  Input registers   (func 4): 0-4 have sensor values")
    print("  Coils             (func 1): 0-3 have relay states")
    print("  Discrete inputs   (func 2): 0-3 have sensor states")
    print()
    print("Test with openmmg (via MQTT):")
    print(f'  mosquitto_pub -t "modbus/request" -m "0 12345 4 127.0.0.1 {args.port} 5 {args.slave_id} 3 0 10"')
    print()
    print("Test directly with modbus_client.py:")
    print(f"  python modbus_client.py --port {args.port}")
    print()

    StartTcpServer(context=context, address=(args.host, args.port))


if __name__ == "__main__":
    main()
