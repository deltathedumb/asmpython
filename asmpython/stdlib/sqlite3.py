"""sqlite3: DB-API 2.0 interface for SQLite databases.

Note: SQLite3 requires C bindings. This module provides the full DB-API
2.0 surface but raises NotImplementedError for all operations that require
the C library. Constants and exception classes are fully accessible.
"""
from __future__ import annotations

# DB-API 2.0 module attributes
apilevel: str = "2.0"
threadsafety: int = 1
paramstyle: str = "qmark"

# SQLite version (stub)
version: str = "2.6.0"
version_info: list = [2, 6, 0]
sqlite_version: str = "3.39.2"
sqlite_version_info: list = [3, 39, 2]

# Type codes
PARSE_DECLTYPES: int = 1
PARSE_COLNAMES: int = 2
SQLITE_OK: int = 0
SQLITE_ERROR: int = 1
SQLITE_INTERNAL: int = 2
SQLITE_PERM: int = 3
SQLITE_ABORT: int = 4
SQLITE_BUSY: int = 5
SQLITE_LOCKED: int = 6
SQLITE_NOMEM: int = 7
SQLITE_READONLY: int = 8
SQLITE_INTERRUPT: int = 9
SQLITE_IOERR: int = 10
SQLITE_CORRUPT: int = 11
SQLITE_NOTFOUND: int = 12
SQLITE_FULL: int = 13
SQLITE_CANTOPEN: int = 14
SQLITE_CONSTRAINT: int = 19
SQLITE_MISMATCH: int = 20
SQLITE_DONE: int = 101
SQLITE_ROW: int = 100

# Type constants
INTEGER: str = "INTEGER"
REAL: str = "REAL"
TEXT: str = "TEXT"
BLOB: str = "BLOB"
NULL: str = "NULL"

# Row factory constants
Row: int = 0  # placeholder


class Error(Exception):
    """Base class for sqlite3 exceptions."""

    def __init__(self, msg: str = "") -> None:
        self.msg: str = msg

    def __str__(self) -> str:
        return self.msg


class Warning(Error):
    pass


class InterfaceError(Error):
    pass


class DatabaseError(Error):
    pass


class InternalError(DatabaseError):
    pass


class OperationalError(DatabaseError):
    pass


class ProgrammingError(DatabaseError):
    pass


class IntegrityError(DatabaseError):
    pass


class DataError(DatabaseError):
    pass


class NotSupportedError(DatabaseError):
    pass


class Cursor:
    """DB-API 2.0 cursor object (stub)."""

    def __init__(self) -> None:
        self.description: list = []
        self.rowcount: int = -1
        self.lastrowid: int = 0
        self.arraysize: int = 1
        self.connection: object = None

    def execute(self, sql: str, parameters: list = []) -> Cursor:
        raise NotImplementedError("sqlite3 requires C binding")

    def executemany(self, sql: str, seq_of_parameters: list = []) -> Cursor:
        raise NotImplementedError("sqlite3 requires C binding")

    def executescript(self, sql_script: str) -> Cursor:
        raise NotImplementedError("sqlite3 requires C binding")

    def fetchone(self) -> list:
        raise NotImplementedError("sqlite3 requires C binding")

    def fetchmany(self, size: int = 1) -> list:
        raise NotImplementedError("sqlite3 requires C binding")

    def fetchall(self) -> list:
        raise NotImplementedError("sqlite3 requires C binding")

    def close(self) -> None:
        pass

    def __enter__(self) -> Cursor:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> int:
        self.close()
        return 0

    def setinputsizes(self, sizes: list) -> None:
        pass

    def setoutputsize(self, size: int, column: int = 0) -> None:
        pass


class Connection:
    """DB-API 2.0 connection object (stub)."""

    def __init__(self, database: str, timeout: float = 5.0,
                 detect_types: int = 0, isolation_level: str = "",
                 check_same_thread: int = 1, factory: int = 0,
                 cached_statements: int = 128, uri: int = 0) -> None:
        self.database: str = database
        self.isolation_level: str = isolation_level
        self.row_factory: int = 0
        self.text_factory: int = 0
        self.total_changes: int = 0
        self.in_transaction: int = 0

    def cursor(self, factory: int = 0) -> Cursor:
        raise NotImplementedError("sqlite3 requires C binding")

    def execute(self, sql: str, parameters: list = []) -> Cursor:
        raise NotImplementedError("sqlite3 requires C binding")

    def executemany(self, sql: str, parameters: list = []) -> Cursor:
        raise NotImplementedError("sqlite3 requires C binding")

    def executescript(self, sql_script: str) -> Cursor:
        raise NotImplementedError("sqlite3 requires C binding")

    def commit(self) -> None:
        raise NotImplementedError("sqlite3 requires C binding")

    def rollback(self) -> None:
        raise NotImplementedError("sqlite3 requires C binding")

    def close(self) -> None:
        pass

    def create_function(self, name: str, num_params: int, func: int,
                        deterministic: int = 0) -> None:
        raise NotImplementedError("sqlite3 requires C binding")

    def create_aggregate(self, name: str, num_params: int, aggregate_class: int) -> None:
        raise NotImplementedError("sqlite3 requires C binding")

    def create_collation(self, name: str, callable: int) -> None:
        raise NotImplementedError("sqlite3 requires C binding")

    def set_authorizer(self, authorizer_callback: int) -> None:
        raise NotImplementedError("sqlite3 requires C binding")

    def set_progress_handler(self, handler: int, n: int) -> None:
        raise NotImplementedError("sqlite3 requires C binding")

    def set_trace_callback(self, trace_callback: int) -> None:
        raise NotImplementedError("sqlite3 requires C binding")

    def enable_load_extension(self, enabled: int) -> None:
        raise NotImplementedError("sqlite3 requires C binding")

    def load_extension(self, path: str) -> None:
        raise NotImplementedError("sqlite3 requires C binding")

    def iterdump(self) -> list:
        raise NotImplementedError("sqlite3 requires C binding")

    def backup(self, target: Connection, pages: int = -1, progress: int = 0,
               name: str = "main", sleep: float = 0.250) -> None:
        raise NotImplementedError("sqlite3 requires C binding")

    def interrupt(self) -> None:
        raise NotImplementedError("sqlite3 requires C binding")

    def __enter__(self) -> Connection:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> int:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return 0


def connect(database: str, timeout: float = 5.0, detect_types: int = 0,
            isolation_level: str = "", check_same_thread: int = 1,
            factory: int = 0, cached_statements: int = 128,
            uri: int = 0) -> Connection:
    """Open a connection to the SQLite database *database* (raises NotImplementedError)."""
    raise NotImplementedError("sqlite3.connect requires C binding")


def complete_statement(sql: str) -> int:
    """Return 1 if *sql* is a complete SQLite statement (stub: always True)."""
    return 1


def enable_callback_tracebacks(flag: int) -> None:
    """Enable/disable tracebacks for user-defined functions (stub)."""
    pass


def register_adapter(type: int, adapter: int) -> None:
    """Register an adapter for a type (stub)."""
    pass


def register_converter(typename: str, converter: int) -> None:
    """Register a converter for a SQLite type name (stub)."""
    pass
