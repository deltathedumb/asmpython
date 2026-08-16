# expect:
# a
# :
# b:c
# hello
#
#

s = "a:b:c"
l, sep, r = s.partition(":")
print(l)
print(sep)
print(r)
s2 = "hello"
l2, sep2, r2 = s2.partition(":")
print(l2)
print(sep2)
print(r2)
