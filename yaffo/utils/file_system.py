import platform
import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass
class ShowFileDialogResult:
    selected_path: Optional[str]
    error: str
    success: bool
    status_code: int


_MACOS_SCRIPT = {
    "folder": 'choose folder with prompt "Select a folder"',
    "file": 'choose file with prompt "Select a file"',
}
_WINDOWS_DIALOG = {
    "folder": (
        '$dialog = New-Object System.Windows.Forms.FolderBrowserDialog;'
        ' $dialog.Description = "Select a folder";'
        ' if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK)'
        ' { Write-Output $dialog.SelectedPath }'
    ),
    "file": (
        '$dialog = New-Object System.Windows.Forms.OpenFileDialog;'
        ' $dialog.Title = "Select a file";'
        ' if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK)'
        ' { Write-Output $dialog.FileName }'
    ),
}


def show_file_dialog(mode: str = "folder") -> ShowFileDialogResult:
    """Open a native picker and return the chosen path. `mode` is "folder" or "file"
    (no single native dialog portably selects either, so they're separate modes)."""
    if mode not in ("folder", "file"):
        mode = "folder"
    selected_path = None
    status_code = 500
    success = False
    error = ""

    try:
        system = platform.system()
        if system == "Darwin":  # macOS
            script = (
                'tell application "System Events"\n'
                "    activate\n"
                f"    set chosen to {_MACOS_SCRIPT[mode]}\n"
                "    return POSIX path of chosen\n"
                "end tell"
            )
            result = subprocess.run(
                ['osascript', '-e', script],
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout for user to select
            )
            if result.returncode == 0:
                selected_path = result.stdout.strip()
                # Remove trailing slash if present
                if selected_path.endswith('/'):
                    selected_path = selected_path[:-1]
        elif system == "Windows":
            script = (
                "Add-Type -AssemblyName System.Windows.Forms; " + _WINDOWS_DIALOG[mode]
            )
            result = subprocess.run(
                ['powershell', '-Command', script],
                capture_output=True,
                text=True,
                timeout=300
            )
            if result.returncode == 0 and result.stdout.strip():
                selected_path = result.stdout.strip()
        else:
            error = f"File browser not available on Linux. Please enter the {mode} path manually."

        if selected_path:
            success = True
            status_code = 200
        else:
            success = False
            status_code = 404

    except subprocess.TimeoutExpired:
        success = False
        status_code = 500
        error = "Selection timed out"
    except Exception as e:
        success = False
        status_code = 500
        error = str(e)

    return ShowFileDialogResult(
        selected_path=selected_path,
        error=error,
        success=success,
        status_code=status_code,
    )
