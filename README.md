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
