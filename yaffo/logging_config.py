import logging
import sys
from photo_organizer.common import ROOT_DIR

LOG_FORMAT = '%(asctime)s - %(name)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s'
DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

HUEY_LOG_FILE = ROOT_DIR / "background_tasks.log"
WEB_LOG_FILE = ROOT_DIR / "photo_organizer.log"


def setup_logger(name: str, log_file: str, level=logging.INFO):
    """
    Set up a logger with file and console handlers.

    Args:
        name: Logger name (e.g., 'background_tasks', 'webapp', 'photo_organizer.background_tasks')
        log_file: Path to log file
        level: Logging level (default: INFO)

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Prevent duplicate handlers if logger already exists
    if logger.handlers:
        return logger

    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

    # File handler
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Prevent propagation to root logger
    logger.propagate = False

    return logger


def get_logger(module_name: str, log_type: str ='webapp', level=logging.INFO):
    """
    Factory method to create a logger for a specific module.

    Args:
        module_name: Name of the module (use __name__ from calling script)
        log_type: Type of logger - 'webapp' or 'background_tasks' (determines which file to log to)
        level: Logging level (default: INFO)

    Returns:
        Configured logger instance with the module name

    Example:
        from photo_organizer.logging_config import get_logger
        logger = get_logger(__name__, 'background_tasks')
        logger.info('Processing started')
    """
    log_file = HUEY_LOG_FILE if log_type == 'background_tasks' else WEB_LOG_FILE
    return setup_logger(module_name, log_file, level)


def get_webapp_logger():
    """Get or create the web application logger."""
    return setup_logger('webapp', WEB_LOG_FILE)


def get_huey_logger():
    """Get or create the Huey task logger."""
    return setup_logger('background_tasks', HUEY_LOG_FILE)


def set_log_level(logger, level_name):
    """
    Set the log level for a logger.

    Args:
        logger: Logger instance
        level_name: Level name as string ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')
    """
    level = getattr(logging, level_name.upper(), logging.INFO)
    logger.setLevel(level)
    for handler in logger.handlers:
        handler.setLevel(level)


# Initialize default loggers
webapp_logger = get_webapp_logger()
huey_logger = get_huey_logger()