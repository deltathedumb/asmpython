# probes: PurePosixPath joins with /
# expect:
# a/b/c.txt
import pathlib

print(str(pathlib.PurePosixPath("a") / "b" / "c.txt"))
