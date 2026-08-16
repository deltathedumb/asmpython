# expect:
# 0
# 10
# 10
# 25

# global declaration: functions can read and write module-level variables.

counter = 0

def increment(n: int):
    global counter
    counter = counter + n

def reset():
    global counter
    counter = 0

print(counter)       # 0
increment(10)
print(counter)       # 10
increment(15)
print(counter - 15)  # 10
print(counter)       # 25
