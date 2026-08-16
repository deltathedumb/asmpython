# expect:
# 3
# 65
# 66
# 67

from io import BytesIO

buf = BytesIO()
buf.write([65, 66, 67])
print(len(buf.getvalue()))

buf.seek(0)
print(buf.read1())
print(buf.read1())
print(buf.read1())
