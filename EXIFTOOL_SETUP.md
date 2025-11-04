# ExifTool Bundling Setup Guide

This guide explains how to bundle ExifTool with your Yaffo application using Briefcase.

## Quick Start

1. Download ExifTool binaries (see instructions below)
2. Place binaries in the appropriate `resources/bin/` directories
3. Build your application with `briefcase build`

## Downloading ExifTool

### For All Platforms

Visit: https://exiftool.org/

### macOS (darwin/)

**Download:**
- Get `Image-ExifTool-12.xx.tar.gz` from https://exiftool.org/

**Installation:**
```bash
# Download and extract
wget https://exiftool.org/Image-ExifTool-12.70.tar.gz
tar -xzf Image-ExifTool-12.70.tar.gz

# Copy exiftool script to resources
cp Image-ExifTool-12.70/exiftool resources/bin/darwin/exiftool

# Make it executable
chmod +x resources/bin/darwin/exiftool
```

**Verification:**
```bash
# Test the bundled exiftool
./resources/bin/darwin/exiftool -ver
```

### Linux (linux/)

**Download:**
- Get `Image-ExifTool-12.xx.tar.gz` from https://exiftool.org/

**Installation:**
```bash
# Download and extract
wget https://exiftool.org/Image-ExifTool-12.70.tar.gz
tar -xzf Image-ExifTool-12.70.tar.gz

# Copy exiftool script to resources
cp Image-ExifTool-12.70/exiftool resources/bin/linux/exiftool

# Make it executable
chmod +x resources/bin/linux/exiftool
```

**Verification:**
```bash
# Test the bundled exiftool
./resources/bin/linux/exiftool -ver
```

### Windows (windows/)

**Download:**
- Get `exiftool-12.xx.zip` from https://exiftool.org/

**Installation (PowerShell):**
```powershell
# Download (or download manually via browser)
Invoke-WebRequest -Uri "https://exiftool.org/exiftool-12.70.zip" -OutFile "exiftool.zip"

# Extract
Expand-Archive -Path "exiftool.zip" -DestinationPath "."

# Rename and copy to resources
Rename-Item "exiftool(-k).exe" "exiftool.exe"
Copy-Item "exiftool.exe" "resources\bin\windows\exiftool.exe"
```

**Verification:**
```powershell
# Test the bundled exiftool
.\resources\bin\windows\exiftool.exe -ver
```

## Building with Briefcase

### First Time Setup

```bash
# Install briefcase if not already installed
pip install briefcase

# Create the app structure
briefcase create

# Update resources (includes exiftool)
briefcase update

# Build the app
briefcase build

# Run the app
briefcase run
```

### Subsequent Builds

```bash
# If you updated exiftool binaries
briefcase update

# Build
briefcase build

# Run
briefcase run
```

## Platform-Specific Package

### macOS App Bundle
```bash
briefcase package macOS
```

The exiftool binary will be located at:
`YaffoPhotoOrganizer.app/Contents/Resources/exiftool`

### Linux AppImage
```bash
briefcase package linux appimage
```

The exiftool binary will be bundled inside the AppImage.

### Windows Installer
```bash
briefcase package windows
```

The exiftool.exe will be included in the installation directory.

## Verifying ExifTool is Bundled

Run your application and check the logs. The application will automatically:
1. First try to use bundled exiftool
2. Fall back to system exiftool if bundled version not found
3. Log which version it's using

You can also verify programmatically:

```python
from yaffo.utils.exiftool_path import get_exiftool_path, is_exiftool_available

print(f"ExifTool available: {is_exiftool_available()}")
print(f"ExifTool path: {get_exiftool_path()}")
```

## Troubleshooting

### macOS: "exiftool cannot be opened because the developer cannot be verified"

This may happen if you download exiftool from the web. To fix:
```bash
xattr -d com.apple.quarantine resources/bin/darwin/exiftool
```

### Linux: "Permission denied"

Make sure exiftool is executable:
```bash
chmod +x resources/bin/linux/exiftool
```

### Windows: "exiftool is not recognized"

Make sure the file is named exactly `exiftool.exe` (not `exiftool(-k).exe`)

### ExifTool not found in bundled app

1. Check that files exist in `resources/bin/`
2. Run `briefcase update` to ensure resources are copied
3. Check `pyproject.toml` has correct resource paths

## License Compliance

ExifTool is distributed under the Perl Artistic License and GPL, which allows bundling and redistribution.

**Important:** Add license attribution to your app:
- Include `THIRD_PARTY_LICENSES.txt` in your app
- Mention ExifTool by Phil Harvey (https://exiftool.org/)
- Include a copy of the Perl Artistic License

## Current ExifTool Version

The instructions above use version 12.70. Check https://exiftool.org/ for the latest version.

## Development vs Production

### Development
When running in development mode, the app will:
1. Check `resources/bin/{platform}/exiftool`
2. Fall back to system exiftool (if installed)

### Production (Briefcase Bundle)
When running as a bundled app, the app will:
1. Use bundled exiftool from the app bundle
2. Fall back to system exiftool (if bundled version missing)

## Testing

After setting up, test the metadata sync feature:
1. Navigate to Utilities → Sync Metadata to Files
2. Select photos with metadata
3. Click "Sync Metadata to Files"
4. Verify metadata was written using:
   ```bash
   exiftool -G path/to/photo.jpg | grep -i person
   ```