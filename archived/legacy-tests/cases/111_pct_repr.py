# expect:
# 'hi'
#       'hi'|'hi'      |
# 5
# repr: 'Bob'

print("%r" % "hi")
print("%10r|%-10r|" % ("hi", "hi"))
print("%r" % 5)
name = "Bob"
print("repr: %r" % name)
