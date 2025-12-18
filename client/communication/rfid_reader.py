import logging
import threading
from datetime import datetime
from typing import Callable, Optional

import serial


class RFIDReader:
    """Background thread that streams RFID serial data via callback."""

    def __init__(self, port: str, baudrate: int = 9600):
        self.port = port
        self.baudrate = baudrate
        self.serial: Optional[serial.Serial] = None
        self.reading = False
        self.thread: Optional[threading.Thread] = None
        self.callback: Optional[Callable] = None
        self.last_card_id: Optional[str] = None

    def start_reading(self, callback: Callable) -> bool:
        """Start the reader thread."""
        try:
            self.serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=8,
                parity='N',
                stopbits=1,
                timeout=0.1,
            )
            self.callback = callback
            self.reading = True
            self.thread = threading.Thread(target=self._read_loop, daemon=True)
            self.thread.start()
            logging.info("RFID reader started port=%s baudrate=%s", self.port, self.baudrate)
            return True
        except Exception as exc:
            logging.error("Failed to start RFID reader: %s", exc)
            return False

    def _read_loop(self):
        """Consume CRLF-terminated frames and emit via callback."""
        while self.reading:
            try:
                if not self.serial or not self.serial.is_open:
                    break

                data = self.serial.read_until(b'\x0D\x0A')
                if not data:
                    continue

                card_id = data.hex(' ')
                if card_id == self.last_card_id:
                    continue

                self.last_card_id = card_id
                payload = {
                    'card_id': card_id,
                    'raw_data': data.hex(),
                    'length': len(data),
                    'timestamp': self._get_timestamp(),
                }
                if self.callback:
                    self.callback(payload)
                logging.info("RFID card detected card_id=%s", card_id)
            except Exception as exc:
                logging.error("RFID read error: %s", exc)
                threading.Event().wait(0.1)

    @staticmethod
    def _get_timestamp() -> str:
        return datetime.now().isoformat()

    def stop_reading(self):
        """Stop the reader thread."""
        self.reading = False
        if self.thread:
            self.thread.join(timeout=2)
        if self.serial and self.serial.is_open:
            self.serial.close()
        logging.info("RFID reader stopped")

    def is_reading(self) -> bool:
        return bool(self.reading and self.serial and self.serial.is_open)
