# probes: PurePosixPath exposes its parent
# expect:
# a/b
import pathlib

print(str(pathlib.PurePosixPath("a/b/c.txt").parent))
