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
            "2.7", #Scrcpy server version
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
        keyframe_recorded = False
        pts_ts = 0
        
        while self.alive:
            try:
                pts = 0
                if self.send_frame_meta:
                    video_header = self._video_socket.recv(12)
                    if len(video_header) != 12:
                        raise ConnectionError("Video header is less than 12 bytes")
                    
                    (pts, data_packet_length) = struct.unpack(">QL", video_header)
                    pts = pts & const.ScrcpyMasks.PACKET_PTS_MASK
                    #print(pts)
                raw_h264 = self._video_socket.recv(65535)
                t = raw_h264[4] & 0x1F
                if t == 5:#keyframe nal
                    keyframe_recorded = True
                elif t != 7 and keyframe_recorded is False:
                    continue
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
        
    def try_close_socket(self, socket):
        if socket is not None:
            try:
                socket.close()
            except Exception:
                pass
        return

    def stop(self) -> None:
        super().stop()
        self.try_close_socket(self._server_stream)
        self.try_close_socket(self.control_socket)
        self.try_close_socket(self._video_socket)
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
    
    test_scrcpy()