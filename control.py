import struct
import const

class ControlSender:
    def __init__(self, parent):
        self.parent = parent
        return

    def get_time(self) -> str:
        """
        Get device system time
        """
        s = self.parent.control_socket

        with self.parent.control_socket_lock:
            # Flush socket
            s.setblocking(False)
            while True:
                try:
                    s.recv(1024)
                except BlockingIOError:
                    break
            s.setblocking(True)

            package = struct.pack(">B", const.ScrcpyControls.GET_CURRENT_TIME)
            s.send(package)
            (code,) = struct.unpack(">B", s.recv(1))
            assert code == 3
            (t,) = struct.unpack(">q", s.recv(8))
            return t
        return