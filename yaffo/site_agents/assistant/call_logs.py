"""Conversation-owned call-log locations and deletion."""
from pathlib import Path
import shutil

from yaffo.common import ROOT_DIR

LOG_ROOT = ROOT_DIR / "assistant_model_logs"


def conversation_log_dir(conversation_id: int) -> Path:
    return LOG_ROOT / str(int(conversation_id))


def delete_logs(conversation_id: int) -> None:
    shutil.rmtree(conversation_log_dir(conversation_id), ignore_errors=True)


def delete_all_logs() -> None:
    shutil.rmtree(LOG_ROOT, ignore_errors=True)
