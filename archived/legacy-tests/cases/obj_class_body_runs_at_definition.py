# probes: a class body executes when defined
# expect:
# body ran
# 6
class Built:
    print("body ran")
    computed = 2 * 3


print(Built.computed)
