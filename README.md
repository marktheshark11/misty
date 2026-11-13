# Misty image recognition

The main program has Misty scan for a face, follow it, and say “Hey!” before sending a picture directly from this PC to Ollama. Ollama returns a specific visible detail, such as “gray shirt” or “curly hair,” and Misty says “I like your [detail].” If no clear detail is found, Misty says nothing further. Face detection and tracking run on this PC rather than on Misty (see [Face tracking](#face-tracking)). No FastAPI server or `robot_image_rec` repo is needed.

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
python .\main.py            # add --preview to see what the face tracker sees
```

The first run takes longer while Ollama loads the model and the face models download (about 40 MB). Misty scans until it finds a face, then follows it while Ollama describes a visible clothing detail. It speaks the result once per person, then resumes scanning when that person is gone. The picture sent to Ollama is also saved as `misty_capture.jpg`. Press `Ctrl+C` to stop, or `q` in the preview window.

## Optional Ollama settings

The defaults are `http://127.0.0.1:11434` and `qwen2.5vl:3b`. To use a different local Ollama host or model for one run:

```powershell
$env:OLLAMA_HOST = "http://127.0.0.1:11434"
$env:OLLAMA_MODEL = "qwen2.5vl:3b"
python .\main.py
```

The model must be installed in Ollama first. For example, run `ollama pull qwen2.5vl:3b` to install the default model.

## Project files

- `main.py` runs the idle scan, face tracking, photo capture, Ollama description, and speech. Run this file.
- `ollama_client.py` sends the image to Ollama and reads the description.
- `settings.py` contains the Misty IP, Ollama settings, and capture image filename.
- `idle_mode.py` is a standalone idle scan that says “Hello” to each person it finds and follows them.
- `follow_face.py` follows one person and describes the scene with Ollama about every 10 seconds.
- `face_session.py` runs the shared face tracking used by the three scripts above.
- `misty_stream.py` streams Misty's camera to this PC over RTSP.
- `face_tracker.py` detects faces and keeps the lock on one person.
- `head_controller.py` points Misty's head at the tracked face.
- `face_models.py` downloads the face models into `models/` on first run.
- `misty_camera.py` takes a single picture with Misty's own camera. The scripts above don't use it, because Misty's camera is busy streaming while they run.

## Face tracking

`main.py`, `idle_mode.py` and `follow_face.py` all use the same face tracking. Misty's built-in face detection is slow and has no notion of *which* person it is following, so the vision runs on this PC instead:

1. Misty streams its camera to this PC over RTSP (640x480, 30 fps by default).
2. This PC detects faces on every frame with OpenCV's YuNet model.
3. It locks onto one person using an SFace face embedding. Other people in view are ignored. If the person is briefly hidden, Misty keeps aiming where they were heading, then waits about 3 seconds for that *same* person before picking someone new.
4. It drives Misty's head directly, about 10 times per second. Each frame is matched with where the head was pointing when the frame was captured, so network delay doesn't make the head overshoot or wobble.

Misty shows `e_joy.jpg` while following someone and `e_defaultcontent.jpg` otherwise. Every script accepts `--preview`, which opens a window with a green box on the person being followed and grey boxes on everyone else.

The first run downloads about 40 MB of face models into `models/`. On exit, each script stops the stream and turns Misty's camera service back on. **While the script runs, Misty's on-board camera features (TakePicture, face detection) are unavailable**, because Misty can't stream and use its camera at the same time. Keep Misty supervised while trying the behavior.

### Tuning

Set these environment variables before running if needed:

| Variable | Default | Meaning |
| --- | --- | --- |
| `MISTY_STREAM_FPS` | `30` | Stream frame rate. The script falls back to 24 if Misty rejects 30. |
| `MISTY_STREAM_BITRATE` | `1500000` | Video bitrate. Lower it on a weak Wi-Fi link. |
| `MISTY_STREAM_LATENCY` | `0.25` | Seconds from Misty's camera to this PC. If the head overshoots and swings back, raise it; if it lags behind a moving face, lower it. |
| `MISTY_CAMERA_HFOV` | `70` | Horizontal field of view of the stream, in degrees. If the head consistently stops short of the face, raise it; if it overshoots, lower it. |
| `MISTY_STREAM_PORT` | `1936` | RTSP port Misty publishes on. |
| `MISTY_HEAD_SPEED` | `30` | Head speed (degrees/second at full velocity) assumed when Misty isn't reporting its head position. Too high makes the head overshoot; too low only makes it slower. |

Every 5 seconds the scripts print a status line: frame rate, processing time, tracker state, where the face is, where the head is, and how many head commands were sent (and failed). Angles are in degrees; positive yaw is Misty's left, negative pitch is up. `head (measured)` means Misty is reporting its actual head position, which gives the best tracking. `head (estimated)` means it isn't, and the head position is estimated from the commands sent. Tracking still works then, but it follows a moving face in steps rather than smoothly.

## Other scripts

`idle_mode.py` runs on its own. Misty slowly looks up and down, then a little left and right. When it finds a face, it says “Hello” and follows that person. Once they're gone, it returns to the idle scan.

`follow_face.py` follows one person without scanning and, while it's following them, describes the scene with Ollama about every 10 seconds. Add `--no-describe` to only follow.

With the Python virtual environment active and `MISTY_IP` set, run:

```powershell
python .\idle_mode.py
python .\follow_face.py
```

Press `Ctrl+C` to stop.

## Troubleshooting

- **`ollama` is not recognized:** reinstall Ollama or open a new PowerShell window after installing it.
- **Connection refused at port 11434:** open the Ollama app, or start `ollama serve` in a separate terminal.
- **Model not found:** run `ollama pull qwen2.5vl:3b`.
- **Misty connection error:** set `MISTY_IP`, check Wi-Fi/network connectivity, and make sure Misty's REST API is reachable from this PC.
- **`No video from rtsp://...`:** make sure no other app is streaming from Misty, and that this PC can reach Misty on port 1936. If a previous run crashed, the camera may still be switched to streaming mode: run the script again and stop it with `Ctrl+C`, or restart Misty.
- **Misty does not speak:** check the robot's volume/audio service and inspect the printed speech status/error.
