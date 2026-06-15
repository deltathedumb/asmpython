# expect:
# hello
# 5
# 1

import os

f = os.fopen("_test_os_255.txt", "w")
os.fputs("hello", f)
os.fclose(f)

f2 = os.fopen("_test_os_255.txt", "r")
out = ""
c = os.fgetc(f2)
while c != -1:
    out = out + chr(c)
    c = os.fgetc(f2)
os.fclose(f2)
print(out)
print(len(out))

exists: int = 1 if os._access("_test_os_255.txt", 0) == 0 else 0
print(exists)
os.remove("_test_os_255.txt")
