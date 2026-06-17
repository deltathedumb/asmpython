"""time module: wall-clock time and process sleep."""
from __future__ import annotations

from . import Func, Const

BINDINGS: dict = {
    # time.time() -> int   (Unix timestamp; matches C time_t = int64)
    "time":         Func(arg_types=(),      ret_type="int", c_name="time"),
    # time.sleep(seconds: int)  —  POSIX sleep(); MinGW provides this too
    "sleep":        Func(arg_types=("int",), ret_type="int", c_name="sleep"),
    # time.sleep_ms(ms: int) — millisecond sleep via inline helper
    "sleep_ms":     Func(arg_types=("int",), ret_type="int", c_name="_time_sleep_ms"),
    # time.clock() -> int  (process CPU ticks; POSIX clock())
    "clock":        Func(arg_types=(),      ret_type="int", c_name="clock"),
    # time.difftime(t2, t1) -> float  (t2 - t1 in seconds). C's difftime
    # returns a double (in xmm0); declaring it int read the wrong register and
    # produced garbage.
    "difftime":     Func(arg_types=("int", "int"), ret_type="float", c_name="difftime"),
    # time.perf_counter() -> float  — high-resolution timer in seconds.
    # Uses clock_gettime(CLOCK_MONOTONIC) on Linux, QueryPerformanceCounter on Windows.
    "perf_counter": Func(arg_types=(), ret_type="float", c_name="_time_perf_counter"),
    # time.monotonic() -> float  — alias for perf_counter (same source)
    "monotonic":    Func(arg_types=(), ret_type="float", c_name="_time_perf_counter"),
    # time.time_ns() -> int  — nanosecond timestamp
    "time_ns":      Func(arg_types=(), ret_type="int", c_name="_time_time_ns"),
    # Constant: CLOCKS_PER_SEC (1000000 on Linux, 1000 on Windows/MSVCRT)
    "CLOCKS_PER_SEC": Const(ty="int", value=1000000, value_windows=1000),
}
