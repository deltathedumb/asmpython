# expect:
# 72
# 105
# 33
# 3
# 0
# 1

# os file-I/O FFI: fopen/fgetc/fclose read a file char-by-char (no buffer
# needed), and _access checks existence. The test writes its own input file
# first via fputc-free means... actually it relies on the runner's CWD, so we
# create the file through the FFI too: open for write, but since we only bind
# read primitives here, the runner provides the file via the stdin block is not
# possible — instead we open THIS source file is also not portable. So: create
# a tiny file using os.system, then read it back.

import os

# Create a known file "hi.txt" containing "Hi!" (echo -n is not portable; use
# printf via the shell, which exists on both targets' test envs).
os.system("printf Hi! > hi.txt")

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
os.system("rm -f hi.txt")
