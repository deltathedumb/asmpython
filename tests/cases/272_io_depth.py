# expect:
# hello world
# 11
# hello
# 6
# hello

import io

buf = io.StringIO()
buf.write("hello ")
buf.write("world")
print(buf.getvalue())
print(len(buf.getvalue()))

buf2 = io.StringIO("hello\nworld\n")
line1: str = buf2.readline()
print(line1[:5])
print(buf2.tell())
full: str = buf2.getvalue()
print(full[:5])
