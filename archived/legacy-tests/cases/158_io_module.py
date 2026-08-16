# expect:
# hello world
# hello
# 5
# hello world

from io import StringIO

buf = StringIO()
buf.write("hello ")
buf.write("world")
print(buf.getvalue())

buf2 = StringIO("hello world")
print(buf2.read(5))
print(buf2.tell())
buf2.seek(0)
print(buf2.read())
