from collections import deque

class MatchingConsumer:
    def __init__(self, frame_queue_limit=20, gaze_queue_limit=200, tolerance=0.005): #TODO tolerance check not enough
        self.frame_queue = deque()
        self.gaze_queue = deque()
        self.current_frame = None
        self.current_gaze = None
        self.frame_queue_limit = frame_queue_limit
        self.gaze_queue_limit = gaze_queue_limit
        self.tolerance = tolerance
        self.curr_pts_frame = 0
        return
        
    def try_consume(self, queue):
        return queue.popleft() if len(queue) > 0 else None
        
    def next_match(self):
        out_frame = None
        out_gaze = None

        #try consume next if needed
        if self.current_frame is None:
            self.current_frame = self.try_consume(self.frame_queue)
        if self.current_gaze is None:
            self.current_gaze = self.try_consume(self.gaze_queue)

        if self.current_frame is not None:
            #4 possible states, frame should wait, gaze should wait, match was found or no data
            self.curr_pts_frame = self.current_frame[2]

            # 4 possible states, frame should wait, gaze should wait, match was found or no data
            frame_wait = False
            gaze_wait = False
            match_found = False
            if self.current_gaze is None:
                frame_wait = True
                print("No gaze")
            else:
                #got frame and gaze, need to compare times
                pts_frame = self.current_frame[0]
                was_frame_ahead = False
                while self.current_gaze is not None:
                    pts_gaze = self.current_gaze[0]
                    if abs(pts_frame - pts_gaze) < self.tolerance:
                        match_found = True
                        break
                    elif pts_frame > pts_gaze:
                        #frame ahead
                        #take next gaze
                        print("Got frame", self.current_frame[0], "Checking gaze", self.current_gaze[0])
                        self.current_gaze = self.try_consume(self.gaze_queue)
                        was_frame_ahead = True
                    elif was_frame_ahead is True:
                        #gaze ahead but prev frame was ahead
                        match_found = True
                        break
                    else:
                        #gaze ahead take another frame
                        gaze_wait = True
                        break
                else:
                    #run out of gaze data
                    frame_wait = True
                    print("Checked all gaze data")
            
            if match_found is True:
                out_frame = self.current_frame
                out_gaze = self.current_gaze
                self.current_frame = None
                self.current_gaze = None
                print("match found", out_frame[0], out_gaze[0])
                print("frame queue len", len(self.frame_queue), "gaze queue len", len(self.gaze_queue))
            elif frame_wait is True:
                #we should wait with frame but if buffer full release it
                if len(self.frame_queue) > self.frame_queue_limit:
                    out_frame = self.current_frame
                    self.current_frame = None
                    print("frame release, buffer full")
                else:
                    print("frame waiting", self.current_frame[0], self.current_gaze[0] if self.current_gaze is not None else "NO DATA")
                self.current_gaze = None
            elif gaze_wait is True:
                out_frame = self.current_frame
                self.current_frame = None
                print("gaze waiting")
            else:
                #no data
                pass

        #keep gaze buffer size under control
        while len(self.gaze_queue) > self.gaze_queue_limit:
            self.current_gaze = self.gaze_queue.popleft()
            
        return out_frame, out_gaze

if __name__ == "__main__":
    import cv2
    import numpy as np
    # Workaround for https://github.com/opencv/opencv/issues/21952
    cv2.imshow("cv/av bug", np.zeros(1))
    cv2.destroyAllWindows()

    from streaming import NeonClient, ScrcpyClient
    import const
    import time
    from adbutils import adb
    import av
    import fractions
    from pathlib import Path

    def main():
        from devices import Neon, Headset
        import argparse
        import sys

        side = 0
        scale = 1

        parser = argparse.ArgumentParser()
        parser.add_argument("-i", "--ip", help="Neon IP", type=str)
        parser.add_argument("-p", "--port", help="Neon port", type=int, default=8080)
        parser.add_argument("-d", "--di", help="Adb device index", type=int, default=0)
        parser.add_argument(
            "-s",
            "--split-fix",
            help="Replace scrcpy cropping with NumPy horizontal split",
            action="store_true",
        )
        parser.add_argument(
            "--save",
            help="Record video to a file",
            type=Path,
            default=None,
            dest="record_video",
        )
        parser.add_argument(
            "--save-vr-gaze",
            help="Record gaze data to a file in screen record coord",
            type=Path,
            default=None,
            dest="record_gaze",
        )
        parser.add_argument(
            "-nr",
            "--neon-recording",
            help="Start a Neon recording",
            action="store_true",
        )
        args = parser.parse_args()

        gaze_saver_thread = None
        gaze_queue_saver = None
        if args.record_gaze:
            import queue
            import threading

            def gaze_saver(q, output_path):
                import numpy as np

                gaze_data = []
                while True:
                    data = q.get()
                    if data is None:  # Sentinel to stop
                        break
                    gaze_data.append(data)
                if gaze_data:
                    np.save(output_path, np.array(gaze_data, dtype=object))

            gaze_queue_saver = queue.Queue()
            gaze_saver_thread = threading.Thread(
                target=gaze_saver, args=(gaze_queue_saver, args.record_gaze)
            )
            gaze_saver_thread.start()

        headset = Headset(scale)
        matcher = MatchingConsumer()
        client_gaze = NeonClient(args.ip, args.port)
        neon = Neon(client_gaze.ip, client_gaze.port)
        device = adb.device_list()[args.di]
        region = f"{headset.img_size[0]}:{headset.img_size[1]}:{headset.img_size[0] * side}:0"
        max_width=headset.target_img_size[0]
        if args.split_fix is True:
            region = None
            max_width = max_width << 1
        client_frame = ScrcpyClient(device=device, max_width=max_width, bitrate=1600000, max_fps=20, send_frame_meta=True, crop=region)
        

        container = None
        if args.record_video:
            container = av.open(args.record_video, mode="w")
            stream = container.add_stream("h264")

        def on_gaze_data(data):
            matcher.gaze_queue.append((data.timestamp_unix_seconds + client_gaze.offset * 0.001, data))
            return
        client_gaze.add_listener(const.PlEvents.GAZE_DATA, on_gaze_data)

        def on_frame(frame, pts):
        def on_frame(frame, pts, pts_ts):
            if args.split_fix is True:
                frame = np.hsplit(frame, 2)[side]
            matcher.frame_queue.append(
                (pts + client_frame.offset * 0.001, frame, pts_ts)
            )
            return

        client_frame.add_listener(const.ScrcpyEvents.FRAME, on_frame)
        
        client_gaze.start()
        client_frame.start()

        time_file = None
        if args.record_gaze:
            time.sleep(3)
            time_file = open(args.record_gaze.with_suffix(".time"), "w")
            client_gaze.device.recording_start()

        is_first_gaze = True

        try:
            while(True):
                frame, gaze = matcher.next_match()
                if frame is not None:
                    undistorted = headset.unwrap(frame[1], side)
                    if gaze is not None:
                        if is_first_gaze and args.record_gaze:
                            is_first_gaze = False
                            estimate = client_gaze.device.estimate_time_offset()
                            clock_offset_ns = round(
                                estimate.time_offset_ms.mean * 1_000_000
                            )

                            current_time_ns_in_client_clock = time.time_ns()
                            current_time_ns_in_companion_clock = (
                                current_time_ns_in_client_clock - clock_offset_ns
                            )
                            print(
                                client_gaze.device.send_event(
                                    "neon_vr_recorder.begin",
                                    event_timestamp_unix_ns=current_time_ns_in_companion_clock,
                                )
                            )

                        gaze_dir = neon.get_gaze_dir(gaze[1])
                        point = headset.wrap(gaze_dir, side)
                        if gaze_queue_saver is not None:
                            gaze_queue_saver.put(
                                (gaze[1].timestamp_unix_seconds, point)
                            )
                        cv2.circle(undistorted, point, 10, (0, 0, 255), 2)

                        if container and frame:
                            undistorted = cv2.cvtColor(undistorted, cv2.COLOR_BGR2RGB)
                            frame = av.VideoFrame.from_ndarray(
                                undistorted, format="rgb24"
                            )

                            for packet in stream.encode(frame):
                                packet.time_base = fractions.Fraction(1, 20)
                                packet.pts = matcher.curr_pts_frame

                                if not stream.width:
                                    stream.width = codec.width
                                    stream.height = codec.height
                                    stream.pix_fmt = "yuv420p"

                                container.mux(packet)

                                if time_file:
                                    ts = gaze[0] if gaze else adjusted_ts
                                    time_file.write(f"{ts}\n")

                    cv2.imshow("frame", undistorted)
                cv2.waitKey(10)
        except KeyboardInterrupt:
            pass
        finally:
            if gaze_saver_thread:
                gaze_queue_saver.put(None)
                gaze_saver_thread.join()
            if time_file:
                time_file.close()
            if args.record_gaze:
                client_gaze.device.send_event("neon_vr_recorder.end")
                client_gaze.device.recording_stop_and_save()
            if container:
                # for packet in stream.encode():
                #    container.mux(packet)

                container.close()
            client_gaze.stop()
            client_frame.stop()
        return
    
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
        import av
        import fractions
        device = adb.device_list()[0]
        client = ScrcpyClient(device=device, max_width=1032,bitrate=1600000, max_fps=20, send_frame_meta=True, crop="2064:2208:0:0")
        
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
    
    main()