# expect:
# bob is 5
# hi bob
#        bob done
# 3.14
# x and y and x
# hello world!
# 00101010
# 1,234,567
# 'bob'
name = "bob"
age = 5
pi = 3.14159
n = 1234567

print("{name} is {age}".format(name=name, age=age))
print("{0} {name}".format("hi", name=name))
print("{name:>10} done".format(name=name))
print("{val:.2f}".format(val=pi))
print("{a} and {b} and {a}".format(a="x", b="y"))
print("{} {greet}!".format("hello", greet="world"))
print("{n:08b}".format(n=42))
print("{amt:,}".format(amt=n))
print("{x!r}".format(x=name))
