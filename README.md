# Misty face greeting

Run `main.py` to have Misty scan for a face, approach to about 1 m, take a picture, wave, introduce herself, and give a brief compliment based on visible clothing. She then asks to be petted until a head touch sensor is activated. When the person is no longer detected, Misty returns to scanning.

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
   python -m pip install -e .\misty-python-sdk requests
   ```

   If PowerShell blocks activation, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in that window, then activate again.

4. Ensure Ollama is running, then start Misty's behavior:

   ```powershell
   python .\main.py
   ```

Press `Ctrl+C` to stop. The program connects to the local Ollama API at `http://127.0.0.1:11434`. Set `OLLAMA_HOST` or `OLLAMA_MODEL` in PowerShell to override the defaults. For example:

```powershell
$env:OLLAMA_HOST = "http://127.0.0.1:11434"
$env:OLLAMA_MODEL = "qwen2.5vl:3b"
```

## Project files

- `main.py` is the program entry point and coordinates scanning, approach, greeting, and cleanup.
- `approach_face.py` handles face tracking and distance control. Adjust its tuning values near the top if needed.
- `face_utils.py` holds shared scan settings and event helpers.
- `misty_camera.py` captures images from Misty.
- `ollama_client.py` requests a short clothing description from local Ollama.
- `settings.py` reads Misty's IP address and the Ollama model name from environment variables.

## Troubleshooting

- If Misty turns or tilts away from a face, change `BEARING_SIGN` or `ELEVATION_SIGN` in `approach_face.py` to `-1`.
- If `ollama` is not recognized, reinstall Ollama or open a new PowerShell window.
- If Ollama refuses a connection, start the Ollama app or run `ollama serve` in a separate terminal.
- If the model is missing, run `ollama pull qwen2.5vl:3b`.
- If Misty cannot connect, check `MISTY_IP` and the network connection.
