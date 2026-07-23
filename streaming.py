import time
import abc
import const
import threading

from pupil_labs.realtime_api.simple import Device
from typing import Any, Callable, Optional, Tuple, Union

import os
import socket
import struct
from control import ControlSender
from adbutils import AdbConnection, AdbError, AdbDevice, Network
from av.codec import CodecContext

def recv_exact(sock, n):
    buf = bytearray(n)
    view = memoryview(buf)
    received = 0
    while received < n:
        chunk = sock.recv_into(view[received:], n - received)
        if chunk == 0:
            # Peer closed or no more data
            raise ConnectionError(f"Socket closed while receiving (got {received} of {n} bytes)")
        received += chunk
    return bytes(buf)

def try_close_socket(sock):
    if sock is not None:
        try:
            sock.close()
        except Exception:
            pass

class StreamClient(abc.ABC):

    def __init__(self, events):
        self.stream_loop_thread = None
        self.alive = False
        self.listeners = {e:[] for e in events}
        self.offset = 0
        return
        
    def start(self, threaded: bool = True) -> None:
        assert self.alive is False
        self.alive = True
        if threaded:
            self.stream_loop_thread = threading.Thread(
                target=self._stream_loop
            )
            self.stream_loop_thread.start()
        else:
            self._stream_loop()
        return
        
    def stop(self) -> None:
        self.alive = False
        if self.stream_loop_thread is not None:
            self.stream_loop_thread.join()
            self.stream_loop_thread = None
        return
    
    @abc.abstractmethod
    def _stream_loop(self) -> None:
        #while self.alive is True
        return
        
    def add_listener(self, cls: str, listener: Callable[..., Any]) -> None:
        self.listeners[cls].append(listener)
        return
        
    def remove_listener(self, cls: str, listener: Callable[..., Any]) -> None:
        self.listeners[cls].remove(listener)
        return
        
    def _send_to_listeners(self, cls: str, *args, **kwargs) -> None:
        for fun in self.listeners[cls]:
            fun(*args, **kwargs)
        return

class NeonClient(StreamClient):
    def __init__(self, ip=None, port=None, device=None):
        super().__init__(const.PlEvents)
        self.ip = ip
        self.port = port
        self.device = device
        return
        
    def _stream_loop(self):
        if self.device is None:
            if self.ip is not None and self.port is not None:
                self.device = Device(self.ip, self.port)
            else:
                self.device = discover_one_device()
            if self.device is None:
                raise ConnectionError("No device found.")
        self.offset = self.device.estimate_time_offset().time_offset_ms.mean
        print("OFFSET", self.offset)
        while self.alive:
            data = self.device.receive_gaze_datum()
            self._send_to_listeners(const.PlEvents.GAZE_DATA, data)
        return
        
    def stop(self):
        super().stop()
        if self.device is not None:
            self.device.close()
        return
        
class ScrcpyClient(StreamClient):
    def __init__(
        self,
        device: AdbDevice,
        max_width: int = 0,
        bitrate: int = 8000000,
        max_fps: int = 0,
        stay_awake: bool = False,
        connection_timeout: int = 5000,
        send_frame_meta: bool = True,
        encoder_name: Optional[str] = None,
        codec_name: Optional[str] = None,
        crop: Optional[str] = None
    ):
        super().__init__(const.ScrcpyEvents)

        # Check Params
        assert max_width >= 0, "max_width must be greater than or equal to 0"
        assert bitrate >= 0, "bitrate must be greater than or equal to 0"
        assert max_fps >= 0, "max_fps must be greater than or equal to 0"
        assert (
            connection_timeout >= 0
        ), "connection_timeout must be greater than or equal to 0"
        assert codec_name in [None, "h264", "h265", "av1"]

        # Params
        self.device = device
        self.max_width = max_width
        self.bitrate = bitrate
        self.max_fps = max_fps
        self.stay_awake = stay_awake
        self.connection_timeout = connection_timeout
        self.send_frame_meta = send_frame_meta
        self.encoder_name = encoder_name
        self.codec_name = codec_name
        self.crop = crop

        self.resolution = None
        self.device_name = None
        self.control = ControlSender(self)

        # Need to destroy
        self._server_stream = None
        self._video_socket = None
        self.control_socket = None
        self.control_socket_lock = threading.Lock()
        return

    def _init_server_connection(self) -> None:
        for _ in range(self.connection_timeout // 100):
            try:
                self._video_socket = self.device.create_connection(
                    Network.LOCAL_ABSTRACT, "scrcpy"
                )
                break
            except AdbError:
                time.sleep(0.1)
                pass
        else:
            raise ConnectionError(f"Failed to connect scrcpy-server after {self.connection_timeout} ms")

        dummy_byte = self._video_socket.recv(1)
        if not len(dummy_byte) or dummy_byte != b"\x00":
            raise ConnectionError("Did not receive Dummy Byte!")

        self.control_socket = self.device.create_connection(
            Network.LOCAL_ABSTRACT, "scrcpy"
        )

        self.device_name = self._video_socket.recv(64).decode("utf-8").rstrip("\x00")
        if not len(self.device_name):
            raise ConnectionError("Did not receive Device Name!")

        res = self._video_socket.recv(12)
        self.resolution = struct.unpack(">HH", res[:4])
        return

    def _deploy_server(self) -> None:
        jar_path = "3rdparty/scrcpy/scrcpy-server.jar"
        jar_name = os.path.basename(jar_path)
        jar_abs_path = os.path.join(
            os.path.abspath(os.path.dirname(__file__)), jar_path
        )
        jar_device_path = f"/data/local/tmp/{jar_name}"
        self.device.sync.push(jar_abs_path, jar_device_path)
        commands = [
            f"CLASSPATH={jar_device_path}",
            "app_process",
            "/",
            "com.genymobile.scrcpy.Server",
            "4.0", #Scrcpy server version
            "log_level=info",
            f"max_size={self.max_width}",
            f"max_fps={self.max_fps}",
            f"video_bit_rate={self.bitrate}",
            f"video_encoder={self.encoder_name}" if self.encoder_name else "video_encoder=OMX.qcom.video.encoder.avc",
            f"video_codec={self.codec_name}" if self.codec_name else "video_codec=h264",
            f"video_codec_options=i-frame-interval=1",
            "tunnel_forward=true",
            f"send_frame_meta={'true' if self.send_frame_meta else 'false'}",
            "control=true",
            "audio=false",
            "show_touches=false",
            "stay_awake=false",
            "power_off_on_close=false",
            "clipboard_autosync=false"
        ]
        if self.crop is not None:
            commands.append(f"crop={self.crop}")

        self._server_stream: AdbConnection = self.device.shell(
            commands,
            stream=True,
        )

        # Wait for server to start
        self._server_stream.read(10)
        return
        
    def _estimate_time_offset(self, number_of_measurements=100):
        diff_total = 0
        start = time.time_ns()
        for i in range(number_of_measurements):
            t1 = self.control.get_time()
            t2 = time.time_ns()
            diff_total += t2 / 1000000 - t1
        roundtrip_avg = (time.time_ns() - start) / 1000000 / number_of_measurements
        return diff_total / number_of_measurements - roundtrip_avg / 2
        
    def _stream_loop(self):
        self._deploy_server()
        self._init_server_connection()
        self._send_to_listeners(const.ScrcpyEvents.INIT)
        
        self.offset = self._estimate_time_offset() #TODO
        print("OFFSET", self.offset)
        
        codec = CodecContext.create("h264", "r")
        pts_ts = 0
        
        while self.alive:
            try:
                pts = 0
                data_packet_length = 65535
                if self.send_frame_meta:
                    video_header = self._video_socket.recv(12)
                    if len(video_header) != 12:
                        raise ConnectionError("Video header is less than 12 bytes")
                    
                    (pts, data_packet_length) = struct.unpack(">QL", video_header)
                    msb = pts >> 63
                    if msb > 0:
                        continue
                    pts = pts & const.ScrcpyMasks.PACKET_PTS_MASK
                    #print(pts)
                raw_h264 = recv_exact(self._video_socket, data_packet_length)

                if len(raw_h264) != data_packet_length:
                    print(len(raw_h264), data_packet_length)

                packets = codec.parse(raw_h264)
                for packet in packets:
                    self._send_to_listeners(const.ScrcpyEvents.PACKET, packet, codec, pts_ts)
                    pts_ts += 1
                    frames = codec.decode(packet)
                    for frame in frames:
                        frame = frame.to_ndarray(format="bgr24")
                        self.resolution = (frame.shape[1], frame.shape[0])
                        self._send_to_listeners(const.ScrcpyEvents.FRAME, frame, pts * 0.001)
            except (ConnectionError, OSError) as e: # Socket Closed
                if self.alive:
                    self._send_to_listeners(const.ScrcpyEvents.DISCONNECT)
                    self.stop()
                    raise e
        return

    def stop(self) -> None:
        super().stop()
        try_close_socket(self._server_stream)
        try_close_socket(self.control_socket)
        try_close_socket(self._video_socket)
        return

class MinScrcpyClient(StreamClient):
    def __init__(
        self,
        device: AdbDevice,
        width: int = 1032,
        height: int = 1104,
        crop: str = "0.0:0.0:0.5:1.0",
        hqbitrate: int = 15000000,
        lqbitrate: int = 5000000,
        port: int = 27183,
        connection_timeout: int = 5000
    ):
        super().__init__(const.ScrcpyEvents)

        self.device = device
        self.width = width
        self.height = height
        self.crop = crop
        self.lqbitrate = lqbitrate
        self.hqbitrate = hqbitrate
        self.port = port
        self.time_port = port + 1
        self.connection_timeout = connection_timeout

        self.resolution = (width, height)
        self._server_stream = None
        self._video_socket = None
        self._adb_drain_thread = None
        return

    def _deploy_server(self) -> None:
        jar_path = "3rdparty/minscrcpy/stream.jar"
        jar_name = os.path.basename(jar_path)
        jar_abs_path = os.path.join(
            os.path.abspath(os.path.dirname(__file__)), jar_path
        )
        jar_device_path = f"/data/local/tmp/{jar_name}"

        if not os.path.exists(jar_abs_path):
            raise FileNotFoundError(f"Server JAR missing: {jar_abs_path}")

        # Push JAR and wake device
        self.device.sync.push(jar_abs_path, jar_device_path)
        self.device.shell("input keyevent KEYCODE_WAKEUP")
        time.sleep(5)

        # Port forwarding
        self.device.forward(f"tcp:{self.port}", f"tcp:{self.port}")
        self.device.forward(f"tcp:{self.time_port}", f"tcp:{self.time_port}")

        # Run QuestScrcpyMini server process
        cmd = (
            f"CLASSPATH={jar_device_path} app_process / "
            f"com.example.QuestScrcpyMini --width {self.width} --height {self.height} "
            f"--crop {self.crop} --port {self.port} --lqbitrate {self.lqbitrate} --hqbitrate {self.hqbitrate}"
        )
        self._server_stream = self.device.shell(cmd, stream=True)

        # Drain ADB stdout buffer in background to prevent device deadlock
        def _drain_adb():
            try:
                while self.alive and self._server_stream:
                    if not self._server_stream.read(1024):
                        break
            except Exception:
                pass
            return

        self._adb_drain_thread = threading.Thread(target=_drain_adb, daemon=True)
        self._adb_drain_thread.start()
        return

    def _init_server_connection(self) -> None:
        for _ in range(self.connection_timeout // 100):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect(("127.0.0.1", self.port))
                self._video_socket = sock
                return
            except (ConnectionError, OSError):
                time.sleep(0.1)
        raise ConnectionError(
            f"Failed to connect to MinScrcpy server at 127.0.0.1:{self.port} after {self.connection_timeout} ms"
        )
        return

    def _estimate_time_offset(self, number_of_measurements: int = 100) -> float:
        for _ in range(self.connection_timeout // 100):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.connect(("127.0.0.1", self.time_port))
                    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

                    diff_total = 0
                    start = time.time_ns()

                    for _ in range(number_of_measurements):
                        s.sendall(b"\x01")
                        data = recv_exact(s, 8)
                        (t1,) = struct.unpack(">q", data)  # Server time in ms
                        t2 = time.time_ns()                # PC time in ns

                        diff_total += (t2 / 1000000.0) - t1

                    roundtrip_avg = (time.time_ns() - start) / 1000000.0 / number_of_measurements
                    return (diff_total / number_of_measurements) - (roundtrip_avg / 2.0)

            except (ConnectionError, OSError):
                time.sleep(0.1)

        print("[MinScrcpyClient] Warning: Could not sync time offset. Defaulting to 0.0 ms.")
        return 0.0

    def _stream_loop(self) -> None:
        try:
            self._deploy_server()
            time.sleep(0.5)

            self.offset = self._estimate_time_offset()
            print("OFFSET", self.offset)

            self._init_server_connection()
            self._send_to_listeners(const.ScrcpyEvents.INIT)

            codec = CodecContext.create("h264", "r")
            pts_ts = 0

            while self.alive:
                video_header = recv_exact(self._video_socket, 12)
                pts_and_flags, data_packet_length = struct.unpack(">qi", video_header)

                pts = pts_and_flags & const.ScrcpyMasks.PACKET_PTS_MASK
                raw_h264 = recv_exact(self._video_socket, data_packet_length)

                packets = codec.parse(raw_h264)
                for packet in packets:
                    self._send_to_listeners(
                        const.ScrcpyEvents.PACKET, packet, codec, pts_ts
                    )
                    pts_ts += 1

                    frames = codec.decode(packet)
                    for frame in frames:
                        frame_arr = frame.to_ndarray(format="bgr24")
                        self.resolution = (frame_arr.shape[1], frame_arr.shape[0])
                        self._send_to_listeners(const.ScrcpyEvents.FRAME, frame_arr, pts * 0.001)

        except (ConnectionError, OSError) as e:
            if self.alive:
                self._send_to_listeners(const.ScrcpyEvents.DISCONNECT)
                self.stop()
                raise e
        return

    def stop(self) -> None:
        super().stop()
        try_close_socket(self._video_socket)
        try_close_socket(self._server_stream)
        self._video_socket = None
        self._server_stream = None

        try:
            self.device.forward_remove(f"tcp:{self.port}")
            self.device.forward_remove(f"tcp:{self.time_port}")
        except Exception:
            pass
        return

if __name__ == "__main__":
    def test_neon():
        client = NeonClient("192.168.1.27", 8080)

        def on_gaze_data(data):
            print(data.timestamp_unix_seconds)
            return

        client.add_listener(const.PlEvents.GAZE_DATA, on_gaze_data)
        
        client.start()
        time.sleep(10)
        client.stop()
        return
        
    def test_scrcpy():
        from adbutils import adb
        import cv2
        import av
        import fractions
        client = ScrcpyClient(device=adb.device_list()[0], max_width=1032,bitrate=1600000, max_fps=20, send_frame_meta=True, crop="2064:2208:0:0")
        
        def on_frame(frame, pts):
            cv2.imshow("frame", frame)
            cv2.waitKey(1)
            return
        
        container = av.open("out.mp4", mode='w')
        stream = container.add_stream('h264')
        def on_packet(packet, codec, pts):
            packet.time_base = fractions.Fraction(1, 20)
            packet.pts = pts
            stream.width = codec.width
            stream.height = codec.height
            stream.pix_fmt = 'yuv420p'
            container.mux(packet)
            return
        
        client.add_listener(const.ScrcpyEvents.FRAME, on_frame)
        client.add_listener(const.ScrcpyEvents.PACKET, on_packet)
        
        client.start()
        time.sleep(10)
        client.stop()
        return

    def test_minscrcpy():
        from adbutils import adb
        import cv2
        import av
        client = MinScrcpyClient(device=adb.device_list()[0])

        def on_frame(frame, pts):
            cv2.imshow("frame", frame)
            cv2.waitKey(1)
            return
        client.add_listener(const.ScrcpyEvents.FRAME, on_frame)
        client.start()
        time.sleep(20)
        client.stop()
        return
    
    test_minscrcpy()