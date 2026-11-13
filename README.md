# Misty image recognition

This script asks Misty to take a picture, sends the image directly from this PC to Ollama, prints Ollama's description, and has Misty say it aloud. No FastAPI server or `robot_image_rec` repo is needed.

## 1. Install and start Ollama

Install Ollama for Windows from [ollama.com/download](https://ollama.com/download), then open the Ollama app. The app runs Ollama's local API in the background.

Open PowerShell and download the vision model used by the script:

```powershell
ollama pull qwen2.5vl:3b
```

You can confirm the model is installed with:

```powershell
ollama list
```

The script expects Ollama's API at `http://127.0.0.1:11434`. Check that it responds:

```powershell
Invoke-RestMethod http://127.0.0.1:11434/api/tags
```

If the Ollama app is not running, start it from the Start menu. If you use Ollama without the app, start its server in a separate PowerShell window with `ollama serve` and leave that window open.

## 2. Set Misty's IP address

In PowerShell, set `MISTY_IP` to Misty's IP address. The PC and Misty need to be on the same network, and the PC must be able to reach that address. This setting lasts for the current PowerShell window:

```powershell
$env:MISTY_IP = "MISTY_IP_ADDRESS"
```

## 3. Set up Python

From this folder, create and activate a virtual environment, then install the Misty SDK and Python dependency:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .\misty-python-sdk -r requirements.txt
```

If PowerShell blocks activation, run this once in that PowerShell window, then activate again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

## 4. Run it

Make sure the Ollama app/server is running and Misty is on and reachable. From this folder, with the virtual environment active, run:

```powershell
python .\main.py
```

The first run takes longer while Ollama loads the model. The script captures an image, saves a copy as `misty_capture.jpg`, prints the description, and asks Misty to speak it.

## Optional Ollama settings

The defaults are `http://127.0.0.1:11434` and `qwen2.5vl:3b`. To use a different local Ollama host or model for one run:

```powershell
$env:OLLAMA_HOST = "http://127.0.0.1:11434"
$env:OLLAMA_MODEL = "qwen2.5vl:3b"
python .\main.py
```

The model must be installed in Ollama first. For example, run `ollama pull qwen2.5vl:3b` to install the default model.

## Project files

- `main.py` connects the steps and has Misty speak the result. Run this file.
- `misty_camera.py` captures the image from Misty and decodes it.
- `ollama_client.py` sends the image to Ollama and reads the description.
- `settings.py` contains the Misty IP, Ollama settings, and capture image filename.
- `follow_face.py` follows one person's face, with vision running on this PC.
- `misty_stream.py` streams Misty's camera to this PC over RTSP.
- `face_tracker.py` detects faces and keeps the lock on one person.
- `head_controller.py` points Misty's head at the tracked face.
- `face_models.py` downloads the face models into `models/` on first run.

## Face following

`follow_face.py` follows one person's face. Misty's built-in face detection is slow and has no notion of *which* person it is following, so this script runs the vision on this PC instead:

1. Misty streams its camera to this PC over RTSP (640x480, 30 fps by default).
2. This PC detects faces on every frame with OpenCV's YuNet model.
3. It locks onto one person using an SFace face embedding. Other people in view are ignored. If the person is briefly hidden, Misty keeps aiming where they were heading, then waits about 3 seconds for that *same* person before picking someone new.
4. It drives Misty's head directly, about 10 times per second. Each frame is matched with where the head was pointing when the frame was captured, so network delay doesn't make the head overshoot or wobble.

While a face is tracked, it also describes the scene with Ollama about every 10 seconds and has Misty say it. Misty shows `e_joy.jpg` while tracking and `e_defaultcontent.jpg` otherwise.

```powershell
python .\follow_face.py            # add --preview to see what the tracker sees
python .\follow_face.py --no-describe
```

The first run downloads about 40 MB of face models into `models/`. Press `Ctrl+C` to stop (or `q` in the preview window). On exit, the script stops the stream and turns Misty's camera service back on. **While the script runs, Misty's on-board camera features (TakePicture, face detection) are unavailable**, because Misty can't stream and use its camera at the same time. Keep Misty supervised while trying the behavior.

### Tuning

Set these environment variables before running if needed:

| Variable | Default | Meaning |
| --- | --- | --- |
| `MISTY_STREAM_FPS` | `30` | Stream frame rate. The script falls back to 24 if Misty rejects 30. |
| `MISTY_STREAM_BITRATE` | `1500000` | Video bitrate. Lower it on a weak Wi-Fi link. |
| `MISTY_STREAM_LATENCY` | `0.25` | Seconds from Misty's camera to this PC. If the head overshoots and swings back, raise it; if it lags behind a moving face, lower it. |
| `MISTY_CAMERA_HFOV` | `70` | Horizontal field of view of the stream, in degrees. If the head consistently stops short of the face, raise it; if it overshoots, lower it. |
| `MISTY_STREAM_PORT` | `1936` | RTSP port Misty publishes on. |

The script prints the frame rate and processing time every 5 seconds. `head pose: measured` means Misty is reporting its actual head position; `commanded` means it fell back to the last positions sent.

## Troubleshooting

- **`ollama` is not recognized:** reinstall Ollama or open a new PowerShell window after installing it.
- **Connection refused at port 11434:** open the Ollama app, or start `ollama serve` in a separate terminal.
- **Model not found:** run `ollama pull qwen2.5vl:3b`.
- **Misty connection error:** set `MISTY_IP`, check Wi-Fi/network connectivity, and make sure Misty's REST API is reachable from this PC.
- **`No video from rtsp://...`:** make sure no other app is streaming from Misty, and that this PC can reach Misty on port 1936. If a previous run crashed, the camera may still be switched to streaming mode: run the script again and stop it with `Ctrl+C`, or restart Misty.
- **Misty does not speak:** check the robot's volume/audio service and inspect the printed speech status/error.
