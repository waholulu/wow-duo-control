"""Persistent KMBox serial session with bounded, device-timed key presses."""
import argparse
import fcntl
import os
import select
import termios
import threading
import time
from contextlib import nullcontext


class KMBox:
    def __init__(self, port='/dev/cu.usbserial-120'):
        self.fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        self.old = None
        self.lock = threading.RLock()
        self.healthy = True
        self.next_input = 0.0
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.old = termios.tcgetattr(self.fd)
            attrs = termios.tcgetattr(self.fd)
            attrs[0] = attrs[1] = attrs[3] = 0
            attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
            attrs[4] = attrs[5] = termios.B115200
            attrs[6][termios.VMIN] = attrs[6][termios.VTIME] = 0
            termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
            termios.tcflush(self.fd, termios.TCIFLUSH)
            # Discard a partial expression left by an interrupted/failed write.
            # A bare newline could execute that previous session's command.
            self.command('\x03')
        except BaseException:
            self.close()
            raise

    def command(self, expression, timeout=1.0, admission=None, _pulse_ms=0):
        if '\n' in expression or '\r' in expression:
            raise ValueError('One REPL expression per command')
        with self.lock:
            if not self.healthy:
                raise RuntimeError('Serial session lost synchronization; reopen before use')
            # Every input shares this boundary, including pointer operations.
            # Firmware may acknowledge a timed key before the key is released.
            time.sleep(max(0, self.next_input - time.monotonic()))
            started = time.monotonic()
            deadline = started + timeout
            payload = (expression + '\r\n').encode('ascii')
            try:
                while payload:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not select.select([], [self.fd], [], remaining)[1]:
                        raise TimeoutError('KMBox write timeout')
                    # Revalidate at the actual write boundary, after readiness
                    # waits. Never hold the runtime's cancellation lock for ACK.
                    with admission() if admission else nullcontext() as written:
                        count = os.write(self.fd, payload)
                        if count <= 0:
                            raise ConnectionError('KMBox write made no progress')
                        if written is not None:
                            written(count, count == len(payload))
                    payload = payload[count:]
                if _pulse_ms:
                    self.next_input = time.monotonic() + _pulse_ms / 1000 + .01
                result = bytearray()
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not select.select([self.fd], [], [], remaining)[0]:
                        raise TimeoutError('KMBox prompt timeout')
                    chunk = os.read(self.fd, 8192)
                    if not chunk:
                        raise ConnectionError('KMBox disconnected')
                    result.extend(chunk)
                    if len(result) > 65536:
                        raise RuntimeError('Unexpectedly large KMBox response')
                    if result.endswith(b'>>> '):
                        response = result.decode(errors='replace')
                        if 'Traceback' in response or 'Error:' in response:
                            raise RuntimeError(response)
                        return response
            except BaseException:
                self.healthy = False
                raise

    def tap(self, key, duration=80, admission=None):
        if (type(key) is not int or type(duration) is not int
                or not 4 <= key <= 231 or not 20 <= duration <= 500):
            raise ValueError('Key must be 4..231; duration 20..500 ms')
        return self.command(f'km.press({key},{duration})', duration / 1000 + 1,
                            admission=admission, _pulse_ms=duration)

    def close(self):
        with self.lock:
            self.healthy = False
            if self.fd is not None:
                try:
                    if self.old is not None:
                        termios.tcsetattr(self.fd, termios.TCSANOW, self.old)
                finally:
                    os.close(self.fd)
                    self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def command(expression, wait=0.7):
    with KMBox() as box:
        response = box.command(expression, timeout=wait)
        print(response)
        return response


def tap(key, duration):
    with KMBox() as box:
        response = box.tap(key, duration)
        print(response)
        return response


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('hid_key', type=int)
    parser.add_argument('--ms', type=int, default=80)
    args = parser.parse_args()
    tap(args.hid_key, args.ms)
