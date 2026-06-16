# probe80: str.partition, str.splitlines, str.translate (gaps)
s = "hello-world-foo"

# partition
before, sep, after = s.partition("-")
print(before)   # hello
print(sep)      # -
print(after)    # world-foo

# rpartition
before2, sep2, after2 = s.rpartition("-")
print(before2)   # hello-world
print(sep2)      # -
print(after2)    # foo

# partition with no match
b3, s3, a3 = "hello".partition("x")
print(b3)    # hello
print(s3)    # (empty)
print(a3)    # (empty)

# splitlines
lines = "line1\nline2\nline3".splitlines()
print(len(lines))   # 3
print(lines[0])     # line1
print(lines[2])     # line3

# str.isdigit, isalpha, isalnum
print("123".isdigit())    # True
print("abc".isdigit())    # False
print("abc".isalpha())    # True
print("123".isalpha())    # False
print("abc123".isalnum()) # True
print("abc!".isalnum())   # False
print("".isdigit())       # False
