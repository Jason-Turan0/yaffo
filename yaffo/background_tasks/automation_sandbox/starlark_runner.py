"""Bounded Starlark evaluation in a disposable interpreter process.

Host calls stay on the caller's thread: SQLAlchemy sessions, transactions and
run context never cross a process boundary. The child receives JSON data only.
"""
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from typing import Any, Callable

import starlark


@dataclass(frozen=True)
class RunLimits:
    timeout_seconds: float = 60.0
    max_host_calls: int = 1000
    max_output_chars: int = 65536
    max_message_bytes: int = 4 * 1024 * 1024

    def __post_init__(self) -> None:
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        for value in (self.max_host_calls, self.max_output_chars, self.max_message_bytes):
            if type(value) is not int or value < 1:
                raise ValueError("run limits must be positive integers")


DEFAULT_LIMITS = RunLimits()


@dataclass(frozen=True)
class StarlarkResult:
    success: bool
    value: Any = None
    output: list[str] = field(default_factory=list)
    error: str | None = None


def validate_starlark(code: str, *, filename: str = "automation.star") -> str | None:
    try:
        starlark.parse(filename, code, starlark.Dialect.extended())
        return None
    except starlark.StarlarkError as exc:
        return str(exc)


def _read_messages(stream: Any, messages: queue.Queue, maximum: int) -> None:
    try:
        while True:
            line = stream.readline(maximum + 1)
            if not line:
                messages.put(None)
                return
            if len(line) > maximum:
                messages.put(ValueError("Sandbox message size limit exceeded"))
                return
            messages.put(json.loads(line))
    except Exception as exc:
        messages.put(exc)


def run_starlark(
    code: str,
    *,
    inputs: dict[str, Any] | None = None,
    functions: dict[str, Callable[..., Any]] | None = None,
    filename: str = "automation.star",
    limits: RunLimits = DEFAULT_LIMITS,
) -> StarlarkResult:
    """Kill runaway evaluation; return failures and partial output as data.

    The deadline includes host-call time. A host call already executing is allowed
    to finish on the owning thread; no further calls run after the deadline. Host
    I/O therefore still needs its own timeouts (killing a DB write mid-commit is
    unsafe). The evaluator itself is always killed at the deadline.
    """
    functions = functions or {}
    output: list[str] = []
    output_size = 0
    calls = 0
    proc = None
    reader = None
    timer = None
    expired = threading.Event()
    deadline = time.monotonic() + limits.timeout_seconds
    env = {**os.environ, "YAFFO_ROLE": "starlark"}
    command = [sys.executable] if getattr(sys, "frozen", False) else [
        sys.executable, "-m", "yaffo.starlark_worker"]
    # The child must find the checkout even when invoked from the UI-tests folder.
    if not getattr(sys, "frozen", False):
        root = str(Path(__file__).resolve().parents[3])
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [root, env.get("PYTHONPATH")]))

    def send(value: Any) -> None:
        encoded = (json.dumps(value, allow_nan=False) + "\n").encode("utf-8")
        if len(encoded) > limits.max_message_bytes:
            raise ValueError("Sandbox message size limit exceeded")
        proc.stdin.write(encoded)
        proc.stdin.flush()

    def timeout() -> None:
        expired.set()
        try:
            proc.kill()
        except OSError:
            pass

    try:
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=env)
        timer = threading.Timer(max(0, deadline - time.monotonic()), timeout)
        timer.daemon = True
        timer.start()
        messages = queue.Queue()
        reader = threading.Thread(target=_read_messages,
                                  args=(proc.stdout, messages, limits.max_message_bytes), daemon=True)
        reader.start()
        send({"code": code, "inputs": inputs or {}, "functions": list(functions),
              "filename": filename, "max_message_bytes": limits.max_message_bytes})
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or expired.is_set():
                raise TimeoutError("Sandbox wall-clock time limit exceeded")
            try:
                message = messages.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError("Sandbox wall-clock time limit exceeded") from None
            if message is None:
                if expired.is_set():
                    raise TimeoutError("Sandbox wall-clock time limit exceeded")
                raise RuntimeError("Sandbox interpreter exited without a result")
            if isinstance(message, Exception):
                raise message
            kind = message.get("kind")
            if kind == "call":
                calls += 1
                if calls > limits.max_host_calls:
                    raise ValueError("Sandbox host-call limit exceeded")
                name = message["name"]
                if name not in functions:
                    raise ValueError("Unknown sandbox host function")
                try:
                    value = functions[name](*message["args"])
                except Exception as exc:
                    send({"error": str(exc)})
                else:
                    if expired.is_set() or time.monotonic() >= deadline:
                        raise TimeoutError("Sandbox wall-clock time limit exceeded")
                    send({"value": value})
            elif kind == "print":
                line = message["text"]
                if output_size + len(line) + 1 > limits.max_output_chars:
                    raise ValueError("Sandbox print output limit exceeded")
                output.append(line)
                output_size += len(line) + 1
                send({"value": None})
            elif kind == "result":
                return StarlarkResult(message["success"], message.get("value"), output, message.get("error"))
            else:
                raise ValueError("Invalid sandbox message")
    except Exception as exc:
        error = "Sandbox wall-clock time limit exceeded" if expired.is_set() else str(exc)
        return StarlarkResult(False, output=output, error=error)
    finally:
        if timer is not None:
            timer.cancel()
        if proc is not None:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            if reader is not None:
                reader.join(timeout=1)
            proc.stdin.close()
            proc.stdout.close()
