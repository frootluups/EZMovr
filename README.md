# EZMovr

A 30mb program that lets you transfer photos and videos from your camera's SD card to your PC.

## Features

- Auto-detects camera SD cards
- Scans for images (JPEG, PNG, BMP, WebP), RAW files (CR2, ARW, NEF, RAF, ORF, RW2, DNG, PEF, X3F), and video (MP4, MOV, AVI, MKV)
- Copy all files or only new ones (SHA-256 deduplication)
- Organize by date (`YYYY/MM/DD`) or custom folder name
- Scrollable photo preview panel
- One-click executable (no Python required)

## Usage

### Run from Python
```bash
pip install -r requirements.txt
python main.py
```

### Build executable
```bash
build.bat
```
Output: `dist\EZMovr.exe`

### Build + sign locally (Smart App Control)
Windows **Smart App Control** blocks unsigned executables like most Python-built apps. To make EZMovr trusted on *this* machine:

```bash
build.bat /sign
```

This creates a self-signed code-signing certificate in your user store, adds it
to the trusted roots, and signs the executable. **Do not distribute** — the
cert is only trusted on this PC. For real distribution you'd need a proper CA
code-signing certificate.

### Build one-click installer
Requires [Inno Setup 7](https://jrsoftware.org/isinfo.php). Build the exe first,
then:

```bash
build.bat
installer\build_installer.bat
```

Output: `installer\Output\EZMovr-setup.exe` (installs to Program Files, creates
Start Menu/desktop shortcuts, removable via Add/Remove Programs).

## Windows Smart App Control / SmartScreen troubleshooting

The executable is unsigned and built with PyInstaller, which some Windows
security heuristics flag. If Smart App Control or SmartScreen blocks it:

1. **Build with `build.bat /sign`** (recommended, per-machine) — signing with a
   cert trusted in your user store lets the app run without warnings.
2. **"Windows protected your PC" prompt** → click **More info** → **Run anyway**.
3. Make sure the exe isn't marked as "downloaded from the internet":
   right-click the file → Properties → if **Unblock** is shown, click it.
4. If Smart App Control is enabled and still blocks the file, copy the exe to a
   new folder and run it once — a fresh copy avoids cached reputation.
5. As a last resort you can turn off Smart App Control entirely:
   Settings > Windows Security > App & browser control (not recommended).

## How it works

1. Insert your camera's SD card
2. Open EZMovr — it detects the card automatically
3. Choose: copy all files or skip duplicates
4. Pick a destination folder
5. Review the preview and click **Copy Files**

## Project structure

```
sd-mover/
├── main.py
├── requirements.txt
├── build.bat
└── sd_mover/
    ├── drive_detector.py   # Detect removable drives
    ├── file_scanner.py     # Scan for media files
    ├── file_copier.py      # Copy with progress
    ├── folder_builder.py   # Create destination folders
    ├── gui.py              # GUI
    ├── onboarding.py       # First-run setup
    └── settings.py         # Persistent settings
```
