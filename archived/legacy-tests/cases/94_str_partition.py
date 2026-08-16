# expect:
# name
# rest of it
# whole line
# x
# 1

# str.partition(sep): 3-tuple (before, sep, after) at the first occurrence,
# or (s, "", "") when sep is absent. Exercises the 3-target unpack form.

key, sep, val = "name: rest of it".partition(": ")
print(key)         # name
print(val)         # rest of it

a, b, c = "whole line".partition(":")
print(a)           # whole line  (sep absent -> before = whole string)
print("x" + b + c) # x           (mid and after are empty)
print(1 if b == "" else 0)  # 1
