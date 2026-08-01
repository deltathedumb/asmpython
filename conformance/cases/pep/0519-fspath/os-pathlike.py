# tier: spec
# ref: library/os.html#os.fspath
# expect:
# plain
# custom/path
# a/b
# True
import os
from pathlib import PurePosixPath

class MyPath:
    def __fspath__(self):
        return "custom/path"

print(os.fspath("plain"))
print(os.fspath(MyPath()))
print(os.fspath(PurePosixPath("a/b")).replace("\\", "/"))
print(isinstance(MyPath(), os.PathLike))
