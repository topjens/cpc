import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
import logging
from contextlib import closing

from cpc import CpcDevice

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_FILE = Path(__file__).resolve().parent / "concentration.sqlite3"
INTERVAL = 1.0
MAX_FAILURES = 5


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def setup_database(conn):
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL") # FULL is better but might wear out the SD. Wearing out SD is worse than losing a couple of datapoints
    conn.execute("""
        CREATE TABLE IF NOT EXISTS measurements (
            id INTEGER PRIMARY KEY,
            timestamp_utc TEXT NOT NULL,
            concentration REAL,
            status TEXT NOT NULL CHECK(status IN ('ok', 'error')),
            error TEXT
        )
    """)
    conn.commit()


def save_measurement(conn, timestamp, concentration=None,
                     status="ok", error=None):
    # A separate durable transaction for every sample.
    with conn:
        conn.execute("""
            INSERT INTO measurements
                (timestamp_utc, concentration, status, error)
            VALUES (?, ?, ?, ?)
        """, (timestamp, concentration, status, error))


def main():
    failures = 0

    with closing(sqlite3.connect(DB_FILE, timeout=30)) as conn:
        setup_database(conn)

        # Open the instrument ONCE. Never recreate it in the loop.
        with CpcDevice("TSI3772", "/dev/serial0") as device:
            next_sample = time.monotonic()

            while True:
                timestamp = utc_now()
                try:
                    concentration = device.get_concentration()
                except Exception as exc:
                    failures += 1
                    # Persist the failed attempt; database errors propagate.
                    save_measurement(
                        conn,
                        timestamp=timestamp,
                        status="error",
                        error=f"{type(exc).__name__}: {exc}",
                    )

                    logger.info(f"{timestamp} acquisition failed: {exc}")

                    if failures >= MAX_FAILURES:
                        try:
                            device.disconnect()
                            device.connect()
                            failures = 0

                        except Exception as e:
                            logger.exception(f"{timestamp} reconnect failed: {e}")

                else:
                    failures = 0
                    save_measurement(
                        conn,
                        timestamp=timestamp,
                        concentration=concentration,
                    )

                    logger.info(f"{timestamp} concentration={concentration}")

                # Schedule against a monotonic clock to avoid cumulative drift.
                next_sample += INTERVAL
                now = time.monotonic()

                # If an acquisition ran late, skip missed slots instead
                # of issuing rapid catch-up measurements.
                if next_sample <= now:
                    missed = int((now - next_sample) // INTERVAL) + 1
                    next_sample += missed * INTERVAL

                time.sleep(max(0.0, next_sample - time.monotonic()))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Measurement stopped by user.")
