### Installing Dependencies and Code
```sh
git clone https://github.com/pupil-labs/neon-vr-recorder.git
cd neon-vr-recorder
python -m pip install -r requirements.txt
```

### Run Neon VR Recorder
Note: Ensure that the VR headset is powered on, connected to the PC via USB cable, with Developer Mode enabled and USB Debugging authorized.

```sh
python record.py
```

### Using wireless mode
Note: The VR headset must be powered on initially, connected to the PC via a USB cable, with Developer Mode enabled and USB Debugging authorized. Don't forget to replace _ip-address-of-device_ with the actual IP address, which can be copied from the output of the second command. 

```sh
adb tcpip 5555
adb shell ip addr show wlan0
adb connect ip-address-of-device:5555
```

Note: You can now disconnect the VR headset from the PC. Ensure the VR headset is awake before starting the application.

```sh
python record.py
```
