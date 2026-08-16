# expect:
# hello world
# done
# 5
# done bytes

import io

with io.StringIO() as buf:
    buf.write("hello ")
    buf.write("world")
    print(buf.getvalue())
print("done")

with io.BytesIO() as b:
    b.write_byte(1)
    b.write_byte(2)
    b.write_byte(3)
    b.write_byte(4)
    b.write_byte(5)
    print(len(b))
print("done bytes")
