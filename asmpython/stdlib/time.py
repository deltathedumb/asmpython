"""time module: wall-clock time and process sleep."""
from __future__ import annotations

from . import Func

BINDINGS: dict = {
    # time.time() -> int   (Unix timestamp; matches C time_t = int64)
    "time":         Func(arg_types=(),      ret_type="int", c_name="time"),
    # time.sleep(seconds: int)  —  POSIX sleep(); MinGW provides this too
    "sleep":        Func(arg_types=("int",), ret_type="int", c_name="sleep"),
    # time.clock() -> int  (process CPU ticks; POSIX clock())
    "clock":        Func(arg_types=(),      ret_type="int", c_name="clock"),
    # time.difftime(t2, t1) -> float  (t2 - t1 in seconds). C's difftime
    # returns a double (in xmm0); declaring it int read the wrong register and
    # produced garbage.
    "difftime":     Func(arg_types=("int", "int"), ret_type="float", c_name="difftime"),
}
