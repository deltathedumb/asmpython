"""logging module: flexible event logging.

Pure-Python implementation of the logging API. Log records are formatted
and printed to stdout. The hierarchy (loggers, handlers, filters) is
simplified: all loggers share one global level and format.
"""
from __future__ import annotations


DEBUG: int = 10
INFO: int = 20
WARNING: int = 30
WARN: int = 30
ERROR: int = 40
CRITICAL: int = 50
FATAL: int = 50
NOTSET: int = 0


_root_level: int = 30
_format: str = "%(levelname)s:%(name)s:%(message)s"


def _level_name(level: int) -> str:
    if level >= CRITICAL:
        return "CRITICAL"
    if level >= ERROR:
        return "ERROR"
    if level >= WARNING:
        return "WARNING"
    if level >= INFO:
        return "INFO"
    if level >= DEBUG:
        return "DEBUG"
    return "NOTSET"


def _format_record(name: str, level: int, msg: str) -> str:
    result: str = _format
    result = result.replace("%(levelname)s", _level_name(level))
    result = result.replace("%(name)s", name)
    result = result.replace("%(message)s", msg)
    result = result.replace("%(levelno)s", str(level))
    return result


def basicConfig(level: int = 30, format: str = "") -> None:
    """Configure the root logger."""
    global _root_level
    global _format
    _root_level = level
    if len(format) > 0:
        _format = format


def getLevelName(level: int) -> str:
    """Return the textual representation of a logging level."""
    return _level_name(level)


class Handler:
    """Base handler class (outputs to stdout)."""

    def __init__(self, level: int = 0) -> None:
        self.level: int = level

    def emit(self, name: str, level: int, msg: str) -> None:
        if level >= self.level:
            print(_format_record(name, level, msg))

    def setLevel(self, level: int) -> None:
        self.level = level

    def setFormatter(self, fmt: int) -> None:
        pass


class StreamHandler(Handler):
    """Handler that writes to stdout."""
    pass


class FileHandler(Handler):
    """Handler that writes to a file (stub: writes to stdout)."""

    def __init__(self, filename: str, mode: str = "a",
                 level: int = 0) -> None:
        self.filename: str = filename
        self.mode: str = mode
        self.level = level


class NullHandler(Handler):
    """Handler that does nothing."""

    def emit(self, name: str, level: int, msg: str) -> None:
        pass


class Formatter:
    """Format log records."""

    def __init__(self, fmt: str = "", datefmt: str = "") -> None:
        self.fmt: str = fmt if len(fmt) > 0 else _format
        self.datefmt: str = datefmt

    def format(self, name: str, level: int, msg: str) -> str:
        result: str = self.fmt
        result = result.replace("%(levelname)s", _level_name(level))
        result = result.replace("%(name)s", name)
        result = result.replace("%(message)s", msg)
        result = result.replace("%(levelno)s", str(level))
        return result


class Filter:
    """Base filter class (always allows)."""

    def __init__(self, name: str = "") -> None:
        self.name: str = name

    def filter(self, name: str, level: int, msg: str) -> int:
        return 1


class Logger:
    """A named logging channel."""

    def __init__(self, name: str, level: int = 0) -> None:
        self.name: str = name
        self.level: int = level
        self._handlers: list = []
        self.propagate: int = 1
        self.disabled: int = 0

    def setLevel(self, level: int) -> None:
        self.level = level

    def getEffectiveLevel(self) -> int:
        if self.level != NOTSET:
            return self.level
        return _root_level

    def isEnabledFor(self, level: int) -> int:
        return 1 if level >= self.getEffectiveLevel() else 0

    def addHandler(self, handler: Handler) -> None:
        self._handlers.append(handler)

    def removeHandler(self, handler: Handler) -> None:
        new_handlers: list = []
        i: int = 0
        while i < len(self._handlers):
            if self._handlers[i] != handler:
                new_handlers.append(self._handlers[i])
            i = i + 1
        self._handlers = new_handlers

    def _log(self, level: int, msg: str) -> None:
        if self.disabled == 1:
            return
        if level < self.getEffectiveLevel():
            return
        if len(self._handlers) > 0:
            i: int = 0
            while i < len(self._handlers):
                h: Handler = self._handlers[i]
                h.emit(self.name, level, msg)
                i = i + 1
        else:
            print(_format_record(self.name, level, msg))

    def debug(self, msg: str) -> None:
        self._log(DEBUG, msg)

    def info(self, msg: str) -> None:
        self._log(INFO, msg)

    def warning(self, msg: str) -> None:
        self._log(WARNING, msg)

    def warn(self, msg: str) -> None:
        self._log(WARNING, msg)

    def error(self, msg: str) -> None:
        self._log(ERROR, msg)

    def critical(self, msg: str) -> None:
        self._log(CRITICAL, msg)

    def exception(self, msg: str) -> None:
        self._log(ERROR, msg)

    def log(self, level: int, msg: str) -> None:
        self._log(level, msg)


_loggers: list = []
_logger_names: list = []

_root_logger: Logger = Logger("root", 30)


def getLogger(name: str = "root") -> Logger:
    """Return a logger with the specified name."""
    if name == "root" or name == "":
        return _root_logger
    i: int = 0
    while i < len(_logger_names):
        if _logger_names[i] == name:
            return _loggers[i]
        i = i + 1
    lg: Logger = Logger(name)
    _loggers.append(lg)
    _logger_names.append(name)
    return lg


def debug(msg: str) -> None:
    _root_logger.debug(msg)


def info(msg: str) -> None:
    _root_logger.info(msg)


def warning(msg: str) -> None:
    _root_logger.warning(msg)


def warn(msg: str) -> None:
    _root_logger.warn(msg)


def error(msg: str) -> None:
    _root_logger.error(msg)


def critical(msg: str) -> None:
    _root_logger.critical(msg)


def log(level: int, msg: str) -> None:
    _root_logger.log(level, msg)


def disable(level: int = 50) -> None:
    """Disable all logging calls of severity <= level."""
    global _root_level
    _root_level = level + 1


def addLevelName(level: int, levelName: str) -> None:
    """Associate level with levelName (stub, no-op)."""
    pass


def makeLogRecord(d: int) -> Logger:
    """Create a Logger from a dict (stub)."""
    return _root_logger
