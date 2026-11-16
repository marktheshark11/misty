# Misty face greeting

Run `main.py` to have Misty scan for a face. When she sees one, she turns her head toward the person and lets it settle, says “I see you,” takes a picture, and starts approaching while the picture is analyzed. At the estimated 1–1.5 m range, she stops, waves, and says “Hi, I'm Misty!” If the picture analysis is still running, she says “Trying to think of a compliment.” If the first photo gives no usable compliment, she takes one more photo and tries again. She gives the compliment when a visible clothing detail is available, then waits two seconds before asking to be petted until a head touch sensor is activated. After a head pet, Misty says “Thank you!”, displays `e_love.jpg`, and stays still in that state until the program is stopped.

There are two versions of the program. They run the same interaction from the same code and differ only in where faces are detected:

- `main.py` uses Misty's on-board face detection.
- `main_remote.py` detects faces on this PC from Misty's video stream, which is faster and keeps following the same person when others are in view (see [Face detection on this PC](#face-detection-on-this-pc)).

The main loop uses six states: scanning, photo capture, approaching, arrived/greeting/compliment, waiting for a pet, and petted. If no face is found, Misty keeps scanning. Losing the face during an interaction cancels that interaction before scanning resumes. If face distance is unavailable for 10 seconds during approach, Misty stops and returns to scanning. After a failed photo or greeting, Misty waits 10 seconds before trying the visible face again. Press both front leg bump sensors together to cancel the current interaction and reset to idle scanning.

## Setup

1. Install [Ollama for Windows](https://ollama.com/download), start it, and download the vision model:

   ```powershell
   ollama pull qwen2.5vl:3b
   ```

2. Set Misty's IP address in PowerShell. The PC and robot must be on the same network:

   ```powershell
   $env:MISTY_IP = "MISTY_IP_ADDRESS"
   ```

3. Create the virtual environment and install dependencies:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   python -m pip install -e .\misty-python-sdk -r requirements.txt
   ```

   If PowerShell blocks activation, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in that window, then activate again.

4. Ensure Ollama is running, then start Misty's behavior:

   ```powershell
   python .\main.py           # face detection on Misty
   python .\main_remote.py    # face detection on this PC; add --preview to see what it sees
   ```

Press `Ctrl+C` to stop. The program connects to the local Ollama API at `http://127.0.0.1:11434`. Set `OLLAMA_HOST` or `OLLAMA_MODEL` in PowerShell to override the defaults. For example:

```powershell
$env:OLLAMA_HOST = "http://127.0.0.1:11434"
$env:OLLAMA_MODEL = "qwen2.5vl:3b"
```

## Project files

- `main.py` is the entry point for on-board face detection, and holds the scanning, approach, greeting, and cleanup loop that both versions run.
- `main_remote.py` is the entry point for face detection on this PC.
- `remote_approach.py` feeds the PC's face tracker into the approach logic in place of Misty's face events.
- `face_session.py`, `face_tracker.py`, `misty_stream.py`, and `face_models.py` stream Misty's camera to this PC, detect faces, and keep the lock on one person.
- `head_controller.py` points Misty's head at a tracked face. `main_remote.py` doesn't use it, because it moves the head and body the same way as `main.py`.
- `head_check.py` is a diagnostic that tests whether Misty's head responds to commands.
- `greeting.py` handles the first spoken line, photo capture and analysis during approach, the arrival greeting and compliment, and the petting interaction.
- `approach_face.py` handles face tracking and distance control. Adjust its tuning values near the top if needed.
- `face_utils.py` holds shared scan settings and event helpers.
- `misty_camera.py` captures images from Misty (`main_remote.py` takes its picture from the video stream instead).
- `ollama_client.py` requests a short clothing description from local Ollama.
- `settings.py` reads Misty's IP address and the Ollama model name from environment variables.

## Face detection on this PC

With `main_remote.py`, Misty streams her camera to this PC over RTSP (640x480, 24 fps by default). The PC turns each frame upright, detects faces with OpenCV's YuNet model, and locks onto one person with an SFace face embedding, so other people in view are ignored. The face's position and apparent size replace the bearing, elevation, and distance that Misty's own face events provide; the turning, driving, and stopping logic is the same code as in `main.py`.

The first run downloads about 40 MB of face models into `models/`. On exit, the script stops the stream and turns Misty's camera service back on. If a run crashes, the camera may be left in streaming mode: run the script again and stop it with `Ctrl+C`, or restart Misty.

Every 5 seconds the script prints the frame rate, processing time, tracker state, and how far the face is from the centre of the picture. With `--preview`, a window shows a green box on the person being followed and grey boxes on everyone else; press `q` in it to stop.

Set these environment variables before running if needed:

| Variable | Default | Meaning |
| --- | --- | --- |
| `MISTY_STREAM_ROTATE` | `auto` | Degrees to turn each frame clockwise so people are upright: `0`, `90`, `180`, `270`, or `auto`. The script prints the value it found; set it to skip the check. |
| `MISTY_STREAM_TRANSPORT` | `tcp` | `tcp` or `udp`. Try `udp` if the video freezes for a moment now and then; go back to `tcp` if the picture smears. |
| `MISTY_STREAM_FPS` | `24` | Stream frame rate (Misty accepts 2 to 29). |
| `MISTY_STREAM_BITRATE` | `1500000` | Video bitrate. Lower it on a weak Wi-Fi link. |
| `MISTY_CAMERA_FOV` | `70` | Field of view across the longer side of the picture, in degrees. |
| `MISTY_STREAM_PORT` | `1936` | RTSP port Misty publishes on. |

Two values near the top of `remote_approach.py` tune the approach for this version. `FACE_WIDTH_M` sets the distance estimate, which comes from how wide the face appears: compare the printed distance with a measured one, and lower the value if it reads too far. `ANGLE_SCALE` sets how strongly Misty turns and tilts toward the face.

## Troubleshooting

- With `main_remote.py`, if you see `No video` or nothing listening on the stream port, make sure no other app is streaming from Misty and that this PC can reach Misty on port 1936.

- If Misty turns or tilts away from a face, change `BEARING_SIGN` or `ELEVATION_SIGN` in `approach_face.py` to `-1`.
- With `main.py`, `FACE_DISTANCE_SCALE` in `approach_face.py` converts the face event's raw distance to metres. It defaults to `0.01` (assuming centimetres). Compare the printed camera estimate with an actual measured distance and adjust the scale if needed: `actual distance / raw distance`.
- If `ollama` is not recognized, reinstall Ollama or open a new PowerShell window.
- If Ollama refuses a connection, start the Ollama app or run `ollama serve` in a separate terminal.
- If the model is missing, run `ollama pull qwen2.5vl:3b`.
- If Misty cannot connect, check `MISTY_IP` and the network connection.
