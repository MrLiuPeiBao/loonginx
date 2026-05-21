import serial
import time

cmd = bytes.fromhex("19 05 00 00 00 00 CE 12")
port = "/dev/ttyS5"
print("PORT", port)
print("TX", cmd.hex(" "))

ser = serial.Serial(
    port=port,
    baudrate=9600,
    bytesize=8,
    parity="N",
    stopbits=1,
    timeout=0.5,
    write_timeout=0.5,
)
try:
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    t0 = time.time()
    n = ser.write(cmd)
    ser.flush()
    time.sleep(0.05)
    rx = ser.read(64)
    t1 = time.time()
    print("WROTE", n)
    print("ELAPSED_MS", int((t1 - t0) * 1000))
    print("RX_LEN", len(rx))
    print("RX", rx.hex(" ") if rx else "NONE")
finally:
    ser.close()
