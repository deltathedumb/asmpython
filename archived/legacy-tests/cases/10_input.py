# stdin:
# Claude
# 42
# expect:
# name: hello, Claude
# n: 84
name = input("name: ")
print("hello,", name)
n = int(input("n: "))
print(n * 2)
