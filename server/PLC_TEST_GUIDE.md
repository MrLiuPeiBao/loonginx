# PLC Timing Trace Guide

## 1. Enable Timing Trace

Set these values in `[server/.env](C:/Users/lpb/Desktop/loonginx/server/.env)`:

```env
PLC_TIMING_TRACE_ENABLED=true
PLC_TIMING_TRACE_LOG=logs/plc_timing.log
PLC_TIMING_TRACE_INCLUDE_POLL=true
PLC_TIMING_TRACE_INCLUDE_HEADERS=true
```

Restart GUI, API, and `plc_rt` after changing the config.

## 2. Trigger One Real Control Command

Use the GUI PLC control page and send one command.

The GUI log will print:

- `request_id`
- `trace_id`

Keep that `request_id` for analysis.

## 3. Analyze the Timeline

```powershell
$env:PYTHONPATH='C:\Users\lpb\Desktop\loonginx\server'
python C:\Users\lpb\Desktop\loonginx\server\scripts\analyze_plc_timing.py --request-id <request_id>
```

Or inspect the latest command:

```powershell
$env:PYTHONPATH='C:\Users\lpb\Desktop\loonginx\server'
python C:\Users\lpb\Desktop\loonginx\server\scripts\analyze_plc_timing.py --last 1
```

## 4. How To Read The Result

Key metrics:

- `click_to_tx_send_ms`
  Meaning: operator click to actual Modbus `sendall()`
- `db_prelog_ms`
  Meaning: API pre-log database write time
- `queue_wait_ms`
  Meaning: command waiting inside `plc_rt` before `plc-io-thread` dequeues it
- `connect_ms`
  Meaning: Modbus reconnect/connect latency
- `plc_response_ms`
  Meaning: Modbus TX to RX latency
- `pulse_reset_ms`
  Meaning: pulse-reset hold time

Fast diagnosis:

- `queue_wait_ms` is the largest
  Cause: command is queued behind poll/heartbeat work
- `connect_ms` is the largest
  Cause: reconnect or network establishment is slow
- `plc_response_ms` is the largest
  Cause: PLC response or link latency is slow
- `click_to_tx_send_ms` is large, but API/queue/connect are small
  Cause: GUI/HTTP/API front segment is the bottleneck

## 5. Expected Stages For One Control Command

You should see a timeline containing at least:

- `gui.click`
- `gui.http_send_start`
- `api.route_enter`
- `api.command_log_db_start`
- `api.execute_command_call_start`
- `svc.enqueue`
- `svc.dequeue`
- `svc.command_start`
- `plc.control_start`
- `plc.control_primary_write_done`
- `modbus.tx_send`

If `modbus.tx_send` is missing, the command did not actually reach the socket send stage.
