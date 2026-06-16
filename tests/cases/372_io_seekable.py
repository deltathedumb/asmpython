# expect:
# 1
# 1
# 1

import io

s = io.StringIO("hello")
print(s.readable())
print(s.writable())
print(s.seekable())
