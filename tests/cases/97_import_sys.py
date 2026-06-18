# expect:
# 9223372036854775807
# asmpython 2.0.0-preview
# 1
import sys

print(sys.maxsize)
print(sys.version)
# getpid() returns a positive integer; just check it's > 0
pid = sys.getpid()  # type: ignore
print(int(pid > 0))
