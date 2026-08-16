# expect:
# hello world
# hello
# 5
# 11

import io

s = io.StringIO()
s.write("hello")
s.write(" world")
s.seek(0)
print(s.read())

s2 = io.StringIO("hello world")
print(s2.read(5))
print(s2.tell())
print(len(s2.getvalue()))
