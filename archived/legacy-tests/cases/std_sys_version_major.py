# probes: sys.version_info reports Python 3
# expect:
# 3
# 3
import sys

print(sys.version_info[0])
print(sys.version_info.major)
