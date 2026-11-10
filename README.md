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
python -m pip install -e .\misty-python-sdk requests
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
- `follow_face.py` is a separate prototype that uses Misty's built-in face-following action.

## Try the face-following prototype

This prototype uses Misty's native `FOLLOW-FACE` action to track and center on a face. It shows `e_joy.jpg` when a face is detected and `e_defaultcontent.jpg` when no face has been seen recently. While face events show that a face is currently visible, it captures an image about every 10 seconds, asks local Ollama for a description, and has Misty speak it. It starts with the head tilted slightly upward and returns to that position when stopped. Ollama must be running with the `qwen2.5vl:3b` model installed. With the Python virtual environment active, run:

```powershell
python .\follow_face.py
```

Place one face in front of Misty in a well-lit area. Press `Ctrl+C` to stop. The program sends `STOP-FOLLOW` and removes the temporary actions it created. Keep Misty supervised while trying the behavior.

## Troubleshooting

- **`ollama` is not recognized:** reinstall Ollama or open a new PowerShell window after installing it.
- **Connection refused at port 11434:** open the Ollama app, or start `ollama serve` in a separate terminal.
- **Model not found:** run `ollama pull qwen2.5vl:3b`.
- **Misty connection error:** set `MISTY_IP`, check Wi-Fi/network connectivity, and make sure Misty's REST API is reachable from this PC.
- **Misty does not speak:** check the robot's volume/audio service and inspect the printed speech status/error.
