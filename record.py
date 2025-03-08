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

    def main():
        from devices import Neon, Headset
        import argparse
        import sys

        side = 0
        scale = 1

        parser = argparse.ArgumentParser()
        parser.add_argument('-i', '--ip', help='Neon IP', type=str)
        parser.add_argument('-p', '--port', help='Neon port', type=int, default=8080)
        parser.add_argument('-d', '--di', help='Adb device index', type=int, default=0)
        args = parser.parse_args()

        headset = Headset(scale)
        matcher = MatchingConsumer()
        client_gaze = NeonClient(args.ip, args.port)
        neon = Neon(client_gaze.ip, client_gaze.port)
        device = adb.device_list()[args.di]
        region = f"{headset.img_size[0]}:{headset.img_size[1]}:0:0"
        client_frame = ScrcpyClient(device=device, max_width=headset.target_img_size[0], bitrate=1600000, max_fps=20, send_frame_meta=True, crop=region)
        
        def on_gaze_data(data):
            matcher.gaze_queue.append((data.timestamp_unix_seconds + client_gaze.offset * 0.001, data))
            return
        client_gaze.add_listener(const.PlEvents.GAZE_DATA, on_gaze_data)

        def on_frame(frame, pts):
            matcher.frame_queue.append((pts + client_frame.offset * 0.001, frame))
            return
        client_frame.add_listener(const.ScrcpyEvents.FRAME, on_frame)
        
        client_gaze.start()
        client_frame.start()
        
        try:
            while(True):
                frame, gaze = matcher.next_match()
                if frame is not None:
                    undistorted = headset.unwrap(frame[1], side)
                    if gaze is not None:
                        gaze_dir = neon.get_gaze_dir(gaze[1])
                        point = headset.wrap(gaze_dir, side)
                        cv2.circle(undistorted, point, 10, (0, 0, 255), 2)
                    cv2.imshow("frame", undistorted)
                cv2.waitKey(10)
        except KeyboardInterrupt:
            pass
        
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