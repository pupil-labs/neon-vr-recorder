import numpy as np
import cv2
from scipy.spatial.transform import Rotation
import os
import json
import urllib.request

def euler_to_rot(theta, degrees=True) :
    r = Rotation.from_euler("zxy", (-theta[2], -theta[0], theta[1]), degrees)
    return r
    
def normalize(vec):
    return vec / np.linalg.norm(vec)

class Headset:
    def __init__(self, scale, calib_path = f"data/headset.json"):
        self.calib = None
        with open(calib_path, 'r') as calib_file:
            self.calib = json.load(calib_file)
        
        full_resolution = self.calib["resolution"]
        res = np.array((full_resolution[0]*scale, full_resolution[1]*scale)).astype(int)
        img_size = (res[0]//2, res[1])
        scaling_mat = [
            [scale, 1, scale],
            [1, scale, scale],
            [1, 1, 1]
        ]
        
        self.P = (
            np.array(self.calib["P1"]),
            np.array(self.calib["P2"])
        )
        
        self.maps = tuple(
            cv2.initUndistortRectifyMap(
                np.multiply(self.calib[f"{side}CameraMatrix"], scaling_mat),
                np.array(self.calib[f"{side}DistCoeffs"]),
                np.array(self.calib[f"R{i+1}"]),
                self.P[i],
                img_size,
                cv2.CV_16SC2
            ) for i, side in enumerate(("left", "right"))
        )
        return
    
    def unwrap(self, frame, side):
        imgremap = cv2.remap(frame, self.maps[side][0], self.maps[side][1], cv2.INTER_LINEAR)
        return imgremap
        
    def wrap(self, dir_vec, side):
        dir_vec_h = np.append(dir_vec, 1)
        projected_point = self.P[side] @ dir_vec_h
        return (projected_point / projected_point[2]).astype(int)[:2]
        
class Neon:
    def __init__(self, ip, port, config_path="data/neon.json"):
        self.ip = ip
        self.port = port

        calib_path = f"data/{self.get_module_serial()}.bin"
        if os.path.exists(calib_path) is False:
            self.download_intrinsics(calib_path)
        self.intrinsics = self.read_intrinsics(calib_path)

        self.config = None
        with open(config_path, 'r') as config_file:
            self.config = json.load(config_file)

        euler_angles = np.array(self.config["rotation"])
        self.rotation = euler_to_rot(euler_angles)
        return
        
    def get_gaze_dir(self, gaze):
        cm = self.intrinsics["scene_camera_matrix"][0]
        dcs = self.intrinsics["scene_distortion_coefficients"][0]
        points_mat = np.array((((gaze.x, gaze.y),),), dtype=np.float32)
        gaze_dir = cv2.undistortPoints(points_mat, cm, dcs).ravel()
        gaze_dir = np.append(gaze_dir, 1)
        return normalize(self.rotation.apply(gaze_dir))
        
    def get_module_serial(self):
        response = urllib.request.urlopen(f"http://{self.ip}:{self.port}/api/status")
        parsed = json.load(response)
        for result in parsed["result"]:
            if result["model"] == "Hardware":
                return result["data"]["module_serial"]
        return None
        
    def download_intrinsics(self, path):
        with urllib.request.urlopen(f"http://{self.ip}:{self.port}/calibration.bin") as f_source:
            with open(path, "wb") as f_dest:
                f_dest.write(f_source.read())
        return
    
    def read_intrinsics(self, path):
        return np.fromfile(
            path,
            np.dtype(
                [
                    ("version", "u1"),
                    ("serial", "6a"),
                    ("scene_camera_matrix", "(3,3)d"),
                    ("scene_distortion_coefficients", "8d"),
                    ("scene_extrinsics_affine_matrix", "(4,4)d"),
                    ("right_camera_matrix", "(3,3)d"),
                    ("right_distortion_coefficients", "8d"),
                    ("right_extrinsics_affine_matrix", "(4,4)d"),
                    ("left_camera_matrix", "(3,3)d"),
                    ("left_distortion_coefficients", "8d"),
                    ("left_extrinsics_affine_matrix", "(4,4)d"),
                    ("crc", "u4"),
                ]
            )
        )
        
if __name__ == "__main__":
    headset = Headset(0.5)
    print(headset.wrap(np.array((0, 0, 1)), 0))
    neon = Neon("192.168.1.27", 8080)
    from types import SimpleNamespace
    print(neon.get_gaze_dir(SimpleNamespace(x=400,y=400)))