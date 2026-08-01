# probes: errno exposes the standard error numbers
# expect:
# 2
# 17
import errno

print(errno.ENOENT)
print(errno.EEXIST)
