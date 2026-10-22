import threading
import logging
import serial
import math
from typing import Self

logger = logging.getLogger(__name__)

_DEVICE_CONFIGS = {
    "TSI3010": {
        "default_baud": 9600,
        "parity": "EVEN",
        "bytesize": 7,
        "stopbits": 1,
        "encoding": "ascii",
        "read_mode": "readline",
    },

    "TSI3772": {
        "default_baud": 115200,
        "parity": "NONE",
        "bytesize": 8,
        "stopbits": 1,
        "encoding": "utf-8",
        "read_mode": "read_until_cr",
    },
}

class CpcError(Exception): ...
class CpcTimeout(CpcError): ...
class CpcProtocolError(CpcError): ...

class CpcDevice:
    def __init__(
            self,
            cpc_type: str,
            port: str,
            baud: int | None = None,
    ):
        if not port:
            raise ValueError("port cannot be `None'")
        self.port = port
        self.cpc_type = cpc_type
        if self.cpc_type not in _DEVICE_CONFIGS:
            raise ValueError(f"Unknown CPC type '{self.cpc_type}'. Supported: {list(_DEVICE_CONFIGS)}")
        self._cfg = _DEVICE_CONFIGS[self.cpc_type]
        self.baud = int(baud if baud is not None else self._cfg["default_baud"])
        self._lock = threading.Lock()
        self._connected = False
        self._serial = None

    def connect(self) -> None:
        with self._lock:
            if self._connected:
                return
            try:
                parity_map = {
                    "EVEN": serial.PARITY_EVEN,
                    "ODD": serial.PARITY_ODD,
                    "NONE": serial.PARITY_NONE,
                }
                bytesize_map = {
                    6: serial.SIXBITS,
                    7: serial.SEVENBITS,
                    8: serial.EIGHTBITS,
                }
                stopbits_map = {
                    1: serial.STOPBITS_ONE,
                    2: serial.STOPBITS_TWO,
                }
                self._serial = serial.Serial(
                    self.port,
                    self.baud,
                    timeout=1,
                    parity=parity_map[self._cfg["parity"]],
                    bytesize=bytesize_map[self._cfg["bytesize"]],
                    stopbits=stopbits_map[self._cfg["stopbits"]],
                )
                self._connected = True
                logger.info("CpcDevice (%s): connected to %s at %s baud.", self.cpc_type, self.port, self.baud)

            except Exception:
                self._connected = False
                if self._serial is not None:
                    try:
                        self._serial.close()
                    except Exception:
                        pass
                    self._serial = None
                raise

    def disconnect(self) -> None:
        with self._lock:
            if self._serial is not None:
                try:
                    self._serial.close()
                except Exception:
                    logger.warning("Disconnect failed", exc_info=True)
                self._serial = None
            self._connected = False

    def __enter__(self) -> Self:
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.disconnect()

    def _format_query(self) -> bytes:
        return b"RD\r"

    def _read_response(self) -> bytes:
        if self._cfg["read_mode"] == "read_until_cr":
            data = self._serial.read_until(b"\r")
            terminator = b"\r"
        else:
            data = self._serial.readline()
            terminator = b"\n"

        if not data.endswith(terminator):
            raise CpcTimeout(f"Incomplete response: {data!r}")
        return data

    def _parse_conc_response(self, data: bytes) -> float:
        try:
            val = float(data.decode(self._cfg["encoding"]).strip())

        except (UnicodeDecodeError, ValueError) as e:
            raise CpcProtocolError(f"Bad response {data!r}") from e

        if not math.isfinite(val) or val < 0:
            raise CpcProtocolError(f"Implausible value {val}")

        return val

    def get_concentration(self) -> float:
        with self._lock:
            if not self._connected:
                raise RuntimeError("CpcDevice not connected")

            self._serial.reset_input_buffer()
            self._serial.write(self._format_query())
            return self._parse_conc_response(self._read_response())
