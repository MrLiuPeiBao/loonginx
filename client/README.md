## Sensor Gateway

Python gateway that runs on the Loongson 2K0300 board, polls RS485 sensors, listens to MQTT commands, and publishes parsed results back to the server.

### Environment

1. Create the required virtualenv (`venv_linux`):
   ```bash
   python -m venv venv_linux
   ```
2. Install runtime dependencies:
   ```bash
   ./venv_linux/bin/pip install asyncio minimalmodbus pyserial paho-mqtt python-dotenv
   ```

### Run

```bash
./venv_linux/bin/python main.py
```

Adjust the broker credentials, serial port, and sensor options inside `config.py` before starting the service.

### Command handling

- Subscribes to `sensors/command/request`. Payload supports:
  - Hex string (space/comma separated), pure hex without spaces, or integer list.
  - JSON envelope with `request_id`, `command`/`payload`/`request_hex`/`bytes` fields.
- Publishes responses to `sensors/command/response` with `request_hex`/`response_hex`/`success`/`request_id` (if provided). Invalid payloads return `success=false` and `error` message.

### BMS reliability notes

- `communication/serial_manager.py` now exposes `read_registers_direct`, which constructs Modbus RTU frames manually and reads responses based on the reported byte count so CRC mismatches from the BMS no longer stop the pipeline.
- `config.BMS_CONFIG` gains two tuning knobs:
  - `max_retries`: number of attempts per register.
  - `response_delay`: wait time (seconds) between writing the request and reading the reply.
  - `response_timeout`: total time allowance (seconds) to receive each response frame before declaring timeout.
- Default registers include pack voltage/SOC/status/capacity/power plus:
  - `cell_voltages`: reads 8 consecutive registers (0x00-0x07) and returns a list of floats in volts.
  - `current`: reads register `0x29`, applies `(raw - 30000) * 0.1` to express charge as positive and discharge as negative.
- `sensors/bms_sensors.BMSSensor` uses the new direct read path and lets each register override retry/delay settings when needed.

To extend BMS telemetry (for example the 0x00-0x1F cell voltages or 0x29 current register), add new entries under `BMS_CONFIG["registers"]` and set `registers` to the number of Modbus registers plus the desired multiplier.
