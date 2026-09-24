# Setup Guide: Development Environment

Chronicle transcribes with NVIDIA Parakeet through `onnx-asr` and routes
questions with a local e5 embedding model, both running on ONNX Runtime (CPU).
There is no Whisper, faster-whisper or PyTorch dependency.

1) Python
   - Python 3.12 (64-bit). The pins in `requirements.txt` were verified on it.

2) Create a virtual environment in the project root
       py -3.12 -m venv .venv312
       .venv312\Scripts\activate

   Any `.venv*/` or `venv*/` folder is ignored by git.

3) Install dependencies
       pip install -r requirements-dev.txt

   `requirements-dev.txt` pulls in `requirements.txt` (everything the app
   imports) plus the test tools. Use `requirements.txt` alone for a runtime-only
   install.

   Keep `onnxruntime==1.20.1`: newer versions can fail to import on some
   Windows / Python 3.12 setups with a DLL initialization error.

4) OpenRouter API key
   - The assistant, summaries and live answers call OpenRouter. For
     development, create a `.env` file in the project root:
         OPENROUTER_API_KEY=sk-or-...
   - The key can also be saved from inside the app (Settings > API Key), which
     stores it in the Windows Credential Manager. `.env` / the environment
     variable wins when both are set.

5) Models
   - Two models come from Hugging Face:
     - Parakeet `nemo-parakeet-tdt-0.6b-v3` (speech-to-text)
     - `intfloat/multilingual-e5-small` (session routing embeddings)
   - Download them once with the model manager (resumable; Ctrl+C to stop):
         python -m src.model_manager
     About 2.6 GB for full-precision Parakeet (the default,
     `TRANSCRIPTION['quantization'] = None` in `src/config.py`) plus 0.5 GB
     for e5. Changing the quantization means running this again.
   - When running from source, models already in the default Hugging Face
     cache (`~/.cache/huggingface`) are reused; otherwise they go to
     `models/` in the data folder. Once installed, the app runs with
     `HF_HUB_OFFLINE=1` and never downloads a model mid-session.
   - Optional: set `HF_TOKEN` for faster / higher-limit downloads.

6) Run
       python -m src.main

   User data (`chronicle.db`, `sessions/`, `preferences.json`) lives in the
   project root when running from source. Set `CHRONICLE_DATA_DIR` to put it
   somewhere else.

7) Tests
       python -m pytest tests

8) Building the app folder (BU126)
       powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -SkipInstaller

   One command: it makes a fresh venv in `build\venv` from `requirements.txt`
   plus PyInstaller (never reuse `.venv312`, whose extra packages would be
   bundled), runs `packaging\chronicle.spec` and prints the size of
   `dist\Chronicle\` (about 280 MB). Add `-Test` to also run the unit suite in
   that venv first.
   - `dist\Chronicle\Chronicle.exe` runs on Windows 10/11 x64 with no Python
     installed. No models are bundled: the setup wizard downloads them.
   - The build fails if a database, `sessions\`, `preferences.json`,
     `setup_state.json`, `.env` or a `.wav` ends up in the folder.
   - The version is `APP_VERSION` in `src/config.py`. It sets the .exe
     version resource, the installer name, the setup wizard title and
     Settings > About.
   - `--onedir` on purpose: `--onefile` unpacks everything to a temp folder on
     every launch (slow, and more antivirus false positives).
   - `packaging\hooks\` overrides PyInstaller hooks that do not fit the pinned
     packages (`webrtcvad-wheels`).

9) Building the installer (BU127)
   - Install Inno Setup 6.3 or later (https://jrsoftware.org/isinfo.php). The
     script looks for `ISCC.exe` on PATH, in the default install folders, or
     in `$env:ISCC`.
   - Run the full build:
         powershell -ExecutionPolicy Bypass -File packaging\build.ps1
     It builds `dist\Chronicle\`, then runs `packaging\chronicle.iss` and
     writes `dist\ChronicleSetup-<APP_VERSION>.exe`. The first run downloads
     `vc_redist.x64.exe` from Microsoft into `packaging\redist\` (ignored by
     git) to bundle it.
   - What the installer does:
     - "Install for me only" (default, no admin rights,
       `%LOCALAPPDATA%\Programs\Chronicle`) or "for all users"
       (`C:\Program Files\Chronicle`, admin prompt). The folder page always
       shows, so any path or drive can be chosen.
     - Start-menu shortcut, optional desktop shortcut.
     - Installs the Visual C++ 2015-2022 runtime only if 14.40 or later is
       missing (registry check). On a "just me" install this is the one case
       that shows a UAC prompt.
     - Refuses to install or uninstall while Chronicle runs (it holds the
       `Chronicle-AppRunning` mutex, `APP_MUTEX` in `src/main.py`).
     - "Launch Chronicle" on the last page opens the setup wizard on a first
       install.
     - The `AppId` GUID in `chronicle.iss` must never change: upgrades find the
       previous install through it. User data, models and the API key are not
       in the install folder, so upgrades keep them.
   - The uninstaller removes the app and its shortcuts, then asks (default: No)
     whether to delete the user data too: Chronicle's files in the data folder
     named by `%APPDATA%\Chronicle\location.json`, that pointer file, and the
     saved API key. It only deletes Chronicle's own files and removes the data
     folder only if nothing else is left in it. For an all-users install, the
     question is about the data of the user running the uninstaller.
   - The installer is not code-signed, so Windows SmartScreen shows "Windows
     protected your PC" (More info > Run anyway).

10) Google Calendar sign-in (BU130)
   Chronicle signs in with an OAuth "Desktop app" client from a Google Cloud
   project. To create one (for the bundled Chronicle client, or for your own
   custom client):
   - In https://console.cloud.google.com create a project, then enable the
     "Google Calendar API" (APIs & Services > Library).
   - OAuth consent screen (Google Auth Platform > Branding / Audience / Data
     access): user type "External", add the scope
     `https://www.googleapis.com/auth/calendar.events` (plus `openid` and
     `email`), and while the app is in "Testing" add every Google account that
     will sign in under Test users (at most 100). Until Google verifies the
     app, users see "Google hasn't verified this app" (Continue).
   - Clients > Create client > Application type "Desktop app", then download
     the JSON (it has an `installed` block; a "Web application" client is
     rejected).
   - Where the JSON goes:
     - Chronicle client, development: set `CHRONICLE_GOOGLE_CLIENT_ID` and
       `CHRONICLE_GOOGLE_CLIENT_SECRET` in the environment, or
       save the file as `google_oauth_client.json` in the project root.
     - Chronicle client, build: put `google_oauth_client.json` in the project
       root before running `packaginguild.ps1`; `chronicle.spec` bundles it
       when present. It is git-ignored.
     - Custom client: import the file in the app; it is copied to
       `google_client.json` in the data folder (also git-ignored).
   - The refresh token is stored in Windows Credential Manager (service
     `Chronicle`, user `google_calendar_refresh_token`), never in a file.
