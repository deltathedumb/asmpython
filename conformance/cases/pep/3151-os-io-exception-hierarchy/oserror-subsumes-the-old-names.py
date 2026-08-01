# tier: spec
# ref: library/exceptions.html#OSError
# expect:
# True True
# FileNotFoundError True
# PermissionError True
# IsADirectoryError True
# FileExistsError True
# NotADirectoryError True
# InterruptedError True
# BrokenPipeError True
# TimeoutError True
# 2 No such file
# FileNotFoundError
print(IOError is OSError, EnvironmentError is OSError)
for exc in (FileNotFoundError, PermissionError, IsADirectoryError,
            FileExistsError, NotADirectoryError, InterruptedError,
            BrokenPipeError, TimeoutError):
    print(exc.__name__, issubclass(exc, OSError))
e = OSError(2, "No such file")
print(e.errno, e.strerror)
print(type(OSError(2, "x")).__name__)
