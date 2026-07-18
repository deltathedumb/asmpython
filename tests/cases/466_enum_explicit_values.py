# ext: enum
# expect:
# 10
# 11
# 20

enum Status:
    OK = 10
    WARN
    ERROR = 20

print(Status.OK)
print(Status.WARN)
print(Status.ERROR)
