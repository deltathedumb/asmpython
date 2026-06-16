# expect:
# 10
# 10
# ****hi****
# 10
# 10

s = "hi"
print(len(s.center(10)))
print(len(s.ljust(10)))
print(s.center(10, "*"))
print(len(s.rjust(10)))
print(len(s.zfill(10)))
