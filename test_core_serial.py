"""Transport fault tests use mocked file descriptors; no hardware is opened."""
import contextlib
import threading
import time
import unittest
from unittest.mock import patch

from kmbox_tap import KMBox
from runtime_types import Cancelled, Snapshot, Intent
from runtime_engine import Executor
from vision_state import Observation


class SerialBoundaryTests(unittest.TestCase):
    def test_missing_default_uses_only_unambiguous_usb_serial_port(self):
        attrs=[0,0,0,0,0,0,[0]*32]
        with patch('kmbox_tap.os.path.exists',return_value=False), \
                patch('kmbox_tap.glob.glob',return_value=['/dev/cu.usbserial-1120']), \
                patch('kmbox_tap.os.open',return_value=123) as opened, \
                patch('kmbox_tap.fcntl.flock'),patch('kmbox_tap.termios.tcgetattr',return_value=attrs), \
                patch('kmbox_tap.termios.tcsetattr'),patch('kmbox_tap.termios.tcflush'), \
                patch.object(KMBox,'command',return_value='>>> '):
            KMBox()
        opened.assert_called_once_with('/dev/cu.usbserial-1120',unittest.mock.ANY)
        with patch('kmbox_tap.os.path.exists',return_value=False), \
                patch('kmbox_tap.glob.glob',return_value=['/dev/cu.usbserial-1120','/dev/cu.usbserial-120']):
            with self.assertRaises(FileNotFoundError):KMBox()

    def box(self):
        box=KMBox.__new__(KMBox)
        box.fd=123
        box.old=None
        box.lock=threading.RLock()
        box.healthy=True
        box.next_input=0.
        return box

    def test_key_release_delay_starts_at_wire_write_and_also_gates_mouse(self):
        box=self.box()
        now=[100.]
        writes=[]
        ready_count=[0]
        def ready(readable,writable,exceptional,timeout):
            if writable:
                ready_count[0]+=1
                if ready_count[0]==1:
                    now[0]+=.35
            return readable,writable,exceptional
        def sleep(seconds):
            now[0]+=seconds
        def write(fd,payload):
            writes.append((now[0],payload))
            return len(payload)
        with patch('kmbox_tap.time.monotonic',lambda:now[0]),patch('kmbox_tap.time.sleep',sleep), \
                patch('kmbox_tap.select.select',ready),patch('kmbox_tap.os.write',write), \
                patch('kmbox_tap.os.read',return_value=b'>>> '):
            box.tap(26,100)
            box.command('km.move(1,1)')
        self.assertAlmostEqual(writes[0][0],100.35)
        self.assertAlmostEqual(writes[1][0]-writes[0][0],.11)

    def test_partial_write_rechecks_cancellation_before_next_fragment(self):
        box=self.box()
        writes=[]
        admitted=[True]
        @contextlib.contextmanager
        def gate():
            if not admitted[0]:
                raise Cancelled('cancelled after partial write')
            yield
        def write(fd,payload):
            writes.append(payload)
            admitted[0]=False
            return 2
        with patch('kmbox_tap.select.select',side_effect=lambda r,w,e,t:(r,w,e)), \
                patch('kmbox_tap.os.write',write):
            with self.assertRaises(Cancelled):
                box.command('km.press(26,80)',admission=gate)
        self.assertEqual(len(writes),1)
        self.assertFalse(box.healthy)
        with self.assertRaisesRegex(RuntimeError,'lost synchronization'):
            box.command('km.press(26,80)')

    def test_zero_byte_write_fails_instead_of_busy_looping(self):
        box=self.box()
        with patch('kmbox_tap.select.select',side_effect=lambda r,w,e,t:(r,w,e)), \
                patch('kmbox_tap.os.write',return_value=0) as write:
            with self.assertRaisesRegex(ConnectionError,'no progress'):
                box.command('km.click(1)')
        write.assert_called_once()
        self.assertFalse(box.healthy)

    def test_new_session_discards_partial_repl_command_before_newline(self):
        attrs=[0,0,0,0,0,0,[0]*32]
        with patch('kmbox_tap.os.open',return_value=123),patch('kmbox_tap.fcntl.flock'), \
                patch('kmbox_tap.termios.tcgetattr',return_value=attrs), \
                patch('kmbox_tap.termios.tcsetattr'),patch('kmbox_tap.termios.tcflush'), \
                patch.object(KMBox,'command',return_value='>>> ') as command:
            box=KMBox('mock://never-opened')
        command.assert_called_once_with('\x03')
        self.assertEqual(box.fd,123)

    def test_admission_lock_is_released_before_acknowledgement(self):
        box=self.box()
        cancellation_lock=threading.Lock()
        awaiting_ack=threading.Event()
        release_ack=threading.Event()
        errors=[]
        @contextlib.contextmanager
        def gate():
            with cancellation_lock:
                yield
        def ready(readable,writable,exceptional,timeout):
            if readable:
                awaiting_ack.set()
                release_ack.wait(1)
            return readable,writable,exceptional
        def send():
            try:
                box.command('km.click(1)',admission=gate)
            except Exception as exc:
                errors.append(exc)
        with patch('kmbox_tap.select.select',ready),patch('kmbox_tap.os.write',side_effect=lambda fd,p:len(p)), \
                patch('kmbox_tap.os.read',return_value=b'>>> '):
            thread=threading.Thread(target=send)
            thread.start()
            try:
                self.assertTrue(awaiting_ack.wait(.3))
                self.assertTrue(cancellation_lock.acquire(timeout=.05))
                cancellation_lock.release()
            finally:
                release_ack.set()
                thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors,[])

    def test_partial_cancel_is_logged_as_partial_and_failed_never_sent(self):
        box=self.box()
        class Source:
            def peek(self):
                return Snapshot(1,time.monotonic(),(1,0,0),
                                Observation(valid=True,player_hp=1,player_mana=1),None)
        source=Source()
        rows=[]
        executor=Executor(box,source,lambda kind,**kw:rows.append(kind))
        ready_count=[0]
        def ready(r,w,e,timeout):
            if w:
                ready_count[0]+=1
                if ready_count[0]==2:
                    executor.revoke()
            return r,w,e
        snapshot=source.peek()
        intent=Intent('tap',(26,80),'move',snapshot,'task',executor.epoch,snapshot.captured_at+.5)
        with patch('kmbox_tap.select.select',ready),patch('kmbox_tap.os.write',return_value=2) as write, \
                patch('kmbox_tap.os.close'):
            try:
                with self.assertRaises(Cancelled):
                    executor.submit(intent).result(1)
            finally:
                executor.close()
        write.assert_called_once()
        self.assertEqual(rows,['action_proposed','action_write_partial','action_failed'])


if __name__=='__main__':
    unittest.main()
