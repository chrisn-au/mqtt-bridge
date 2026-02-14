#!/usr/bin/env python3
"""
Modbus TCP test client - reads from the simulated server directly.
Useful for verifying the server works before testing through openmmg.

Usage:
    source .venv/bin/activate
    python modbus_client.py [--port 5020] [--slave-id 1]
"""

import argparse
from pymodbus.client import ModbusTcpClient


def main():
    parser = argparse.ArgumentParser(description="Modbus TCP test client")
    parser.add_argument("--host", default="127.0.0.1", help="Server host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5020, help="Server port (default: 5020)")
    parser.add_argument("--slave-id", type=int, default=1, help="Slave/unit ID (default: 1)")
    args = parser.parse_args()

    client = ModbusTcpClient(args.host, port=args.port)
    if not client.connect():
        print(f"Failed to connect to {args.host}:{args.port}")
        return

    print(f"Connected to {args.host}:{args.port} (slave {args.slave_id})")
    print()

    # Read holding registers (function 3)
    print("=== Holding Registers (func 3) ===")
    sid = args.slave_id
    result = client.read_holding_registers(0, count=10, device_id=sid)
    if not result.isError():
        labels = [
            "Temperature (x10 °C)", "Humidity (x10 %)", "Pressure (hPa)",
            "Voltage (x10 V)", "Current (x100 A)", "Power (W)",
            "Frequency (x10 Hz)", "Serial # pt1", "Serial # pt2", "Status"
        ]
        for i, (val, label) in enumerate(zip(result.registers, labels)):
            print(f"  reg[{i}] = {val:5d}  ({label})")
    else:
        print(f"  Error: {result}")
    print()

    # Read input registers (function 4)
    print("=== Input Registers (func 4) ===")
    result = client.read_input_registers(0, count=5, device_id=sid)
    if not result.isError():
        labels = ["Temperature (x10 °C)", "Humidity (x10 %)", "Pressure (hPa)", "ADC", "Counter"]
        for i, (val, label) in enumerate(zip(result.registers, labels)):
            print(f"  reg[{i}] = {val:5d}  ({label})")
    else:
        print(f"  Error: {result}")
    print()

    # Read coils (function 1)
    print("=== Coils (func 1) ===")
    result = client.read_coils(0, count=4, device_id=sid)
    if not result.isError():
        labels = ["Relay 1", "Relay 2", "Relay 3", "Relay 4"]
        for i, (val, label) in enumerate(zip(result.bits[:4], labels)):
            state = "ON" if val else "OFF"
            print(f"  coil[{i}] = {state}  ({label})")
    else:
        print(f"  Error: {result}")
    print()

    # Read discrete inputs (function 2)
    print("=== Discrete Inputs (func 2) ===")
    result = client.read_discrete_inputs(0, count=4, device_id=sid)
    if not result.isError():
        labels = ["Sensor 1", "Sensor 2", "Sensor 3", "Sensor 4"]
        for i, (val, label) in enumerate(zip(result.bits[:4], labels)):
            state = "ACTIVE" if val else "INACTIVE"
            print(f"  input[{i}] = {state}  ({label})")
    else:
        print(f"  Error: {result}")
    print()

    # Write test: write a value to holding register 9
    print("=== Write Test ===")
    print("  Writing 999 to holding register 9...")
    result = client.write_register(9, 999, device_id=sid)
    if not result.isError():
        # Read it back
        result = client.read_holding_registers(9, count=1, device_id=sid)
        if not result.isError():
            print(f"  Read back: {result.registers[0]} (expected 999)")
    else:
        print(f"  Error: {result}")

    client.close()
    print()
    print("Done.")


if __name__ == "__main__":
    main()
