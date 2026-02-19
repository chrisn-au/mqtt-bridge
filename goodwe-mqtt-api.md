# GoodWe MQTT Bridge API

## Connection

| Setting | Value |
|---------|-------|
| Broker | ubiconex.com:8883 (TLS) |
| Request topic | `goodwe/request/{bridge_id}` |
| Response topic | `goodwe/response/{bridge_id}` |
| bridge_id | `1` = mqtt-bridge, `2` = mqtt-bridge-2 |

## Request / Response Format

**Request:** `<COOKIE> <INVERTER_ID> <COMMAND> [ARGS...]`

**Response (success):** `<COOKIE> OK <data...>`

**Response (error):** `<COOKIE> ERR <message>`

- **COOKIE** - Unique request ID (integer). Returned in the response for matching.
- **INVERTER_ID** - Inverter identifier from config (e.g. `0`).
- String values use underscores in place of spaces (e.g. `Normal_(On-Grid)`).

---

## Text Commands (Recommended)

These use the goodwe library's native protocol and return parsed, typed sensor data as `key=value` pairs.

### info

Query inverter identity.

```
Request:  10001 0 info
Response: 10001 OK model=GW5048D-ES serial=95048ESU223W0259 family=ES
```

| Field | Type | Description |
|-------|------|-------------|
| model | string | Inverter model name |
| serial | string | Serial number |
| family | string | Protocol family: ES, DT, ET |

---

### pv

Query PV solar panel data.

```
Request:  10002 0 pv
Response: 10002 OK vpv1=382.80 ipv1=2.00 ppv1=766 pv1_mode=1 ...
```

| Field | Type | Unit | Description |
|-------|------|------|-------------|
| vpv1 | float | V | PV1 Voltage |
| ipv1 | float | A | PV1 Current |
| ppv1 | int | W | PV1 Power |
| pv1_mode | int | | PV1 Mode (0=Not connected, 1=Connected no power, 2=Producing) |
| pv1_mode_label | string | | PV1 Mode description |
| vpv2 | float | V | PV2 Voltage |
| ipv2 | float | A | PV2 Current |
| ppv2 | int | W | PV2 Power |
| pv2_mode | int | | PV2 Mode code |
| pv2_mode_label | string | | PV2 Mode description |
| ppv | int | W | Total PV Power (PV1 + PV2) |

---

### battery

Query battery data. **ES/EM/BP family only.**

```
Request:  10003 0 battery
Response: 10003 OK vbattery1=49.30 ibattery1=-9.30 pbattery1=-458 battery_soc=11 ...
```

| Field | Type | Unit | Range | Description |
|-------|------|------|-------|-------------|
| vbattery1 | float | V | 40-60 | Battery Voltage |
| ibattery1 | float | A | | Battery Current (negative = charging) |
| pbattery1 | int | W | | Battery Power (negative = charging) |
| battery_mode | int | | | Mode (1=Standby, 2=Discharge, 3=Charge, 4=Off) |
| battery_mode_label | string | | | Mode description |
| battery_soc | int | % | 0-100 | State of Charge |
| battery_soh | int | % | 0-100 | State of Health |
| battery_temperature | float | C | | Temperature |
| battery_status | int | | | Status code |
| battery_charge_limit | int | A | | Charge current limit |
| battery_discharge_limit | int | A | | Discharge current limit |
| battery_error | int | | | Error code (0 = none) |
| battery_warning | int | | | Warning code (0 = none) |

---

### grid

Query grid and load data.

```
Request:  10004 0 grid
Response: 10004 OK vgrid=242.20 igrid=0.60 pgrid=4 fgrid=49.95 ...
```

| Field | Type | Unit | Description |
|-------|------|------|-------------|
| vgrid | float | V | On-grid Voltage |
| igrid | float | A | On-grid Current |
| pgrid | int | W | Grid Export Power (negative = importing) |
| fgrid | float | Hz | Grid Frequency |
| grid_mode | int | | Grid Mode (0=Off, 1=On) |
| grid_mode_label | string | | Grid Mode description |
| vload | float | V | Back-up/Load Voltage |
| iload | float | A | Back-up/Load Current |
| pload | int | W | Load Power |
| fload | float | Hz | Back-up Frequency |
| load_mode | int | | Load Mode code |
| load_mode_label | string | | Load Mode description |
| meter_status | int | | Meter Status code |
| grid_in_out | int | | Import/Export (0=Idle, 1=Exporting, 2=Importing) |
| grid_in_out_label | string | | Import/Export description |
| pback_up | int | W | Back-up Power |
| plant_power | int | W | Total Plant Power |
| house_consumption | int | W | House Consumption |
| meter_power_factor | float | | Meter Power Factor |

---

### energy

Query energy generation and consumption totals.

```
Request:  10005 0 energy
Response: 10005 OK e_day=0.20 e_total=21286.90 h_total=27432 e_load_day=46.80 ...
```

| Field | Type | Unit | Description |
|-------|------|------|-------------|
| e_day | float | kWh | Today's PV Generation |
| e_total | float | kWh | Lifetime PV Generation |
| h_total | int | h | Total hours of operation |
| e_load_day | float | kWh | Today's Load consumption |
| e_load_total | float | kWh | Lifetime Load consumption |
| total_power | int | W | Current Total Power |

---

### system

Query system status, work mode, errors, and diagnostics.

```
Request:  10006 0 system
Response: 10006 OK work_mode=2 work_mode_label=Normal_(On-Grid) temperature=27.10 ...
```

| Field | Type | Unit | Description |
|-------|------|------|-------------|
| work_mode | int | | Energy Mode (0=Wait, 1=Off-Grid, 2=On-Grid, 3=Fault, 4=Flash, 5=Check) |
| work_mode_label | string | | Energy Mode description |
| temperature | float | C | Inverter Temperature |
| error_codes | int | | Error Code bitfield (0 = no errors) |
| effective_work_mode | int | | Effective Work Mode code |
| effective_relay_control | int | | Effective Relay Control |
| diagnose_result | int | | Diagnostic Status Code (bitfield) |
| diagnose_result_label | string | | Diagnostic Status description |

---

### all

Query all runtime sensor data in one request. Returns all fields from pv + battery + grid + energy + system combined.

```
Request:  10007 0 all
Response: 10007 OK vpv1=382.80 ipv1=2.00 ... battery_soc=11 ... vgrid=242.20 ...
```

---

### settings

Query inverter configuration settings (read-only).

```
Request:  10008 0 settings
Response: 10008 OK backup_supply=1 off-grid_charge=1 shadow_scan=0 ...
```

| Field | Type | Unit | Description |
|-------|------|------|-------------|
| backup_supply | int | | Backup supply (0=Disabled, 1=Enabled) |
| off-grid_charge | int | | Off-grid charging (0=Disabled, 1=Enabled) |
| shadow_scan | int | | Shadow scan (0=Disabled, 1=Enabled) |
| grid_export | int | | Grid export limit (0=Disabled, 1=Enabled) |
| capacity | int | Ah | Battery capacity |
| charge_v | float | V | Battery charge voltage |
| charge_i | int | A | Battery charge current limit |
| discharge_i | int | A | Battery discharge current limit |
| discharge_v | float | V | Battery discharge cutoff voltage |
| dod | int | % | Depth of Discharge (0-100) |
| battery_activated | int | | Battery activated (0=No, 1=Yes) |
| power_factor | int | | Power factor (x100) |
| grid_export_limit | int | W | Grid export power limit |
| battery_soc_protection | int | % | Battery SOC protection level |
| work_mode | int | | Work mode (0=General, 1=Off-grid, 2=Backup, 3=Eco) |
| eco_mode_1 | string | | Eco mode schedule group 1 |
| eco_mode_1_switch | int | | Eco mode 1 on/off |
| eco_mode_2 | string | | Eco mode schedule group 2 |
| eco_mode_2_switch | int | | Eco mode 2 on/off |
| eco_mode_3 | string | | Eco mode schedule group 3 |
| eco_mode_3_switch | int | | Eco mode 3 on/off |
| eco_mode_4 | string | | Eco mode schedule group 4 |
| eco_mode_4_switch | int | | Eco mode 4 on/off |

---

## Raw Register Commands

For writing settings or accessing registers directly. Use text commands above for reading runtime data.

### Read Registers

```
Request:  <COOKIE> <INV_ID> 3 <REG> <COUNT>
Response: <COOKIE> OK <val1> <val2> ...
```

Values are raw 16-bit unsigned integers.

### Write Single Register

```
Request:  <COOKIE> <INV_ID> 6 <REG> 1 <VALUE>
Response: <COOKIE> OK
```

### Write Multiple Registers

```
Request:  <COOKIE> <INV_ID> 16 <REG> <COUNT> <VAL1> <VAL2> ...
Response: <COOKIE> OK
```

---

## Writable Settings Registers (ES Family)

| Register | Name | Unit | Description |
|----------|------|------|-------------|
| 45012 | backup_supply | | 0=Disabled, 1=Enabled |
| 45014 | off-grid_charge | | 0=Disabled, 1=Enabled |
| 45016 | shadow_scan | | 0=Disabled, 1=Enabled |
| 45018 | grid_export | | 0=Disabled, 1=Enabled |
| 45022 | capacity | Ah | Battery capacity |
| 45024 | charge_v | V x10 | Charge voltage (e.g. 577 = 57.7V) |
| 45026 | charge_i | A | Charge current limit |
| 45028 | discharge_i | A | Discharge current limit |
| 45030 | discharge_v | V x10 | Discharge cutoff (e.g. 420 = 42.0V) |
| 45032 | dod | % | Depth of Discharge (0-100) |
| 45034 | battery_activated | | 0=No, 1=Yes |
| 45052 | grid_export_limit | W | Grid export power limit |
| 45056 | battery_soc_protection | % | SOC protection level |
| 45066 | work_mode | | 0=General, 1=Off-grid, 2=Backup, 3=Eco |

**WARNING:** Writing incorrect values can affect inverter operation. Use with caution.

---

## Inverter Families

| Family | Models | Battery | Protocol |
|--------|--------|---------|----------|
| ES | GW5048D-ES, GW3648D-ES, EM, BP | Yes | AA55 (proprietary UDP) |
| DT | GW5000D-NS, GW6000-DT, MS, XS | No | Standard Modbus |

- **ES**: Hybrid inverter with battery and backup. Runtime data uses proprietary AA55 protocol.
- **DT**: Grid-tied, no battery. All data via standard Modbus registers.
- Both families work with the text commands (pv, grid, energy, system, all, settings).
