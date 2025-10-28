import platform

_SYSTEM = platform.system().lower()
IS_MAC = _SYSTEM == "darwin"
IS_WINDOWS_64 = _SYSTEM == "windows" and platform.machine().endswith('64')
IS_WINDOWS_32 = _SYSTEM == "windows" and not platform.machine().endswith('64')
IS_LINUX = _SYSTEM == "linux"