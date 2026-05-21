import serial
import time
from datetime import datetime

PORT = "/dev/ttyS6"
BAUD = 9600
DURATION = 30.0

print(f"LISTEN {PORT} {BAUD} 8N1 duration={DURATION}s")
ser = serial.Serial(
    port=PORT,
    baudrate=BAUD,
    bytesize=8,
    parity="N",
    stopbits=1,
    timeout=0.2,
)
try:
    ser.reset_input_buffer()
    t_end = time.time() + DURATION
    total = 0
    chunks = 0
    while time.time() < t_end:
        data = ser.read(256)
        if not data:
            continue
        total += len(data)
        chunks += 1
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"[{ts}] len={len(data)} hex={data.hex(' ')}")
    print(f"SUMMARY chunks={chunks} bytes={total}")
finally:
    ser.close()
