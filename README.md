<p align="center"><img src="icon.png" width="96" alt=""></p>

# Batur Teacher Talking

Always-on caption window for English lessons. Whatever is said appears word by
word in real time — grey while speaking, black when the sentence is finished —
one sentence per line, newest line kept in the middle of the window.

- **☁ Online** — Google Gemini Live Transcribe. Runs on any PC (no GPU, tiny CPU).
- **💻 Local** — Whisper on the PC's NVIDIA GPU / CPU (run from source with `requirements-local.txt`).
- **Audio source** — any microphone, or **Computer sound**: captures what Zoom plays,
  so it works when the student wears headphones.

## Install

**Windows** — open **PowerShell** and paste:

```powershell
irm https://raw.githubusercontent.com/baturdenada/batur-teacher-talking/main/install.ps1 | iex
```

**Mac** (Apple silicon or Intel) — open **Terminal** and paste:

```bash
curl -fsSL https://raw.githubusercontent.com/baturdenada/batur-teacher-talking/main/install.sh | bash
```

Both create a **Batur Teacher Talking** folder on the Desktop with the app inside and
start it. On first start it asks for the Gemini API key. Run the same command again
to update.

Preload the key so the student never sees a prompt:

```powershell
# Windows
$env:BTT_KEY="YOUR-GEMINI-KEY"; irm https://raw.githubusercontent.com/baturdenada/batur-teacher-talking/main/install.ps1 | iex
```
```bash
# Mac
curl -fsSL https://raw.githubusercontent.com/baturdenada/batur-teacher-talking/main/install.sh | BTT_KEY="YOUR-GEMINI-KEY" bash
```

Or download the file for your system from the
[latest release](https://github.com/baturdenada/batur-teacher-talking/releases/latest).

## For the student

1. Open the app — wait for the green **● RECORDING** badge.
2. In the source menu pick the microphone — or, on Windows, **🔊 Computer sound**
   for Zoom lessons with headphones.
3. Mac: allow microphone access when macOS asks. For Zoom-through-headphones on a Mac,
   install the free [BlackHole](https://existential.audio/blackhole/) driver and pick
   it as the source.

Settings, log and key live in `%APPDATA%\BaturTeacherTalking` (Windows) or
`~/Library/Application Support/BaturTeacherTalking` (Mac).

## Run from source

```powershell
pip install -r requirements.txt          # Online mode
pip install -r requirements-local.txt    # + Local (Whisper) mode
pythonw app.py
```

## Publish a new version

Double-click `publish.bat` — it uploads the changed files and GitHub Actions
builds a new release with the `.exe` automatically.
