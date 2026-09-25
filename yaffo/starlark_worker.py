"""Private JSON protocol for the disposable Starlark evaluator; no app imports."""
import ctypes
import json
import os
import sys
from typing import Any, Callable

import starlark

if os.name == "nt":
    import msvcrt


EXTENSIONS = [starlark.LibraryExtension.Json, starlark.LibraryExtension.Map,
              starlark.LibraryExtension.Filter]


def _binary_stream(name: str, descriptor: int, mode: str) -> Any:
    stream = getattr(sys, name)
    if stream is not None:
        return stream.buffer
    # Windowed PyInstaller executables can have no Python stdio objects even
    # though Popen supplied valid OS pipe handles.
    if os.name == "nt":
        get_handle = ctypes.WinDLL("kernel32", use_last_error=True).GetStdHandle
        get_handle.argtypes = [ctypes.c_ulong]
        get_handle.restype = ctypes.c_void_p
        handle = get_handle((-10 if descriptor == 0 else -11) & 0xffffffff)
        descriptor = msvcrt.open_osfhandle(handle, os.O_BINARY | (os.O_RDONLY if descriptor == 0 else os.O_WRONLY))
    return os.fdopen(descriptor, mode, closefd=False)


def main() -> None:
    incoming = _binary_stream("stdin", 0, "rb")
    outgoing = _binary_stream("stdout", 1, "wb")
    request = json.loads(incoming.readline())
    maximum = request["max_message_bytes"]

    def send(message: dict) -> None:
        data = (json.dumps(message, allow_nan=False) + "\n").encode("utf-8")
        if len(data) > maximum:
            raise ValueError("Sandbox message size limit exceeded")
        outgoing.write(data)
        outgoing.flush()

    def exchange(message: dict) -> Any:
        send(message)
        response = json.loads(incoming.readline(maximum + 1))
        if "error" in response:
            raise ValueError(response["error"])
        return response.get("value")

    def host(name: str) -> Callable[..., Any]:
        def call(*args: Any) -> Any:
            return exchange({"kind": "call", "name": name, "args": args})
        return call

    def capture_print(*args: Any) -> None:
        exchange({"kind": "print", "text": " ".join(str(a) for a in args)})

    try:
        module = starlark.Module()
        for name, value in request["inputs"].items():
            module[name] = value
        for name in request["functions"]:
            module.add_callable(name, host(name))
        module.add_callable("print", capture_print)
        ast = starlark.parse(request["filename"], request["code"], starlark.Dialect.extended())
        value = starlark.eval(module, ast, starlark.Globals.extended_by(EXTENSIONS))
        send({"kind": "result", "success": True, "value": value})
    except Exception as exc:
        send({"kind": "result", "success": False, "error": str(exc)[:8000]})


if __name__ == "__main__":
    main()
