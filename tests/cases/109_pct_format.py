# expect:
# Bob is 5
#    42|42   |00042
# 3.14
# ff FF 10
# hi
# pi = 3.142
#         hi|hi        |
# 50%

name = "Bob"
age = 5
print("%s is %d" % (name, age))
print("%5d|%-5d|%05d" % (42, 42, 42))
print("%.2f" % 3.14159)
print("%x %X %o" % (255, 255, 8))
print("%s" % "hi")
pi = 3.14159
print("pi = %.3f" % pi)
print("%10s|%-10s|" % ("hi", "hi"))
print("%d%%" % 50)
