import argparse
import os
import sys
import cv2
import numpy as np
from devices import Headset

class VideoRemapper:

    def __init__(self):
        self.headset = Headset(1)
        self.side = 0 #left eye perspective
        return

    def custom_frame_logic(self, frame: np.ndarray, frame_idx: int) -> np.ndarray:
        return self.headset.unwrap(frame, self.side)

    def process(self, input_path: str, output_path: str):
        """Reads the input video, applies custom_frame_logic, and writes the output MP4."""
        if not os.path.exists(input_path):
            print(
                f"Error: Input file '{input_path}' not found.", file=sys.stderr
            )
            return

        cap = cv2.VideoCapture(input_path)
        if not cap.isOpened():
            print(
                f"Error: OpenCV could not open video file '{input_path}'.",
                file=sys.stderr,
            )
            return

        # Read stream attributes
        fps = int(cap.get(cv2.CAP_PROP_FPS))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        if fps <= 0:
            fps = 60.0

        print(f"Processing '{input_path}' -> '{output_path}'")
        print(
            f"Resolution: {width}x{height} | FPS: {fps:.2f} | Total Frames: {total_frames}"
        )

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        out = cv2.VideoWriter(output_path, fourcc, fps, (width, height))

        if not out.isOpened():
            print(
                f"Error: Could not initialize VideoWriter for '{output_path}'.",
                file=sys.stderr,
            )
            cap.release()
            return

        frame_idx = 0
        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                # Execute your custom processing method
                processed_frame = self.custom_frame_logic(frame, frame_idx)

                # Safety check: enforce exact frame dimensions for VideoWriter
                p_height, p_width = processed_frame.shape[:2]
                if (p_width, p_height) != (width, height):
                    processed_frame = cv2.resize(
                        processed_frame, (width, height)
                    )

                out.write(processed_frame)
                frame_idx += 1

                if frame_idx % 100 == 0 or frame_idx == total_frames:
                    print(
                        f"Processed {frame_idx}/{total_frames} frames ({(frame_idx / total_frames) * 100:.1f}%)"
                    )

        finally:
            cap.release()
            out.release()

        print(f"Success! Saved processed video to '{output_path}'.")


if __name__ == "__main__":
    import shutil
    parser = argparse.ArgumentParser(
        description="Process MP4 video frame-by-frame using a VideoRemapper class."
    )
    parser.add_argument(
        "-i",
        "--input",
        help="Path to input MP4 file",
        default="export/Neon Scene Camera v1 ps1.mp4"
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Path for processed output MP4 file",
        default="export/Neon Scene Camera v1 ps1.mp4",
    )

    args = parser.parse_args()
    dinput = os.path.join(os.path.dirname(args.input), "distorted.mp4") 
    shutil.copy(args.input, dinput)
    # Instantiate and run
    processor = VideoRemapper()
    processor.process(dinput, args.output)