# expect:
# aGVsbG8=
# hello
# NBSWY3DP
# 68656C6C6F

import base64

data: list[int] = [104, 101, 108, 108, 111]  # "hello"
enc: list[int] = base64.b64encode(data)
s: str = ""
i: int = 0
while i < len(enc):
    s = s + chr(enc[i])
    i = i + 1
print(s)

dec: list[int] = base64.b64decode(enc)
s2: str = ""
j: int = 0
while j < len(dec):
    s2 = s2 + chr(dec[j])
    j = j + 1
print(s2)

enc32: list[int] = base64.b32encode(data)
s3: str = ""
k: int = 0
while k < len(enc32):
    s3 = s3 + chr(enc32[k])
    k = k + 1
print(s3)

enc16: list[int] = base64.b16encode(data)
s4: str = ""
m: int = 0
while m < len(enc16):
    s4 = s4 + chr(enc16[m])
    m = m + 1
print(s4)
