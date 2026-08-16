# expect:
# 00042
# -0042
# 12345
# hi***
# hello
# ***hi
# **hi**
# **hi*
# [  hi  ]

print("42".zfill(5))
print("-42".zfill(5))
print("12345".zfill(3))
print("hi".ljust(5, "*"))
print("hello".ljust(3, "*"))
print("hi".rjust(5, "*"))
print("hi".center(6, "*"))
print("hi".center(5, "*"))
print("[" + "hi".center(6) + "]")
