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


def show_file_dialog() -> ShowFileDialogResult:
    """Open a native folder selection dialog using OS-specific commands"""
    selected_path = None
    status_code = 500
    success = False
    error = ""

    try:
        system = platform.system()
        if system == "Darwin":  # macOS
            # Use AppleScript to show folder picker
            script = '''
                    tell application "System Events"
                        activate
                        set folderPath to choose folder with prompt "Select Media Directory"
                        return POSIX path of folderPath
                    end tell
                    '''
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
            # Use PowerShell for Windows
            script = '''
                    Add-Type -AssemblyName System.Windows.Forms
                    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
                    $dialog.Description = "Select Media Directory"
                    $result = $dialog.ShowDialog()
                    if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
                        Write-Output $dialog.SelectedPath
                    }
                    '''
            result = subprocess.run(
                ['powershell', '-Command', script],
                capture_output=True,
                text=True,
                timeout=300
            )
            if result.returncode == 0 and result.stdout.strip():
                selected_path = result.stdout.strip()
        else:
            error ="Folder browser not available on Linux. Please enter path manually."

        if selected_path:
            success = True
            status_code = 200
        else:
            success = False
            status_code = 404

    except subprocess.TimeoutExpired:
        success = False
        status_code = 500
        error ="Folder selection timed out"
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
