# expect:
# 72
# 105
# 33
# 3
# 0
# 1

import os

# Create "hi.txt" using FFI so no shell command is needed (portable across platforms).
fw = os.fopen("hi.txt", "w")
os.fputs("Hi!", fw)
os.fclose(fw)

f = os.fopen("hi.txt", "r")
n = 0
c = os.fgetc(f)
while c != -1:
    print(c)       # 72, 105, 33  (H, i, !)
    n = n + 1
    c = os.fgetc(f)
os.fclose(f)
print(n)                          # 3
print(os._access("hi.txt", 0))    # 0  (exists)
print(1 if os._access("nope_xyz.txt", 0) != 0 else 0)  # 1 (missing)
