# probes: a generator's finally runs at exhaustion
# expect:
# finally ran
# [1, 2]
def guarded():
    try:
        yield 1
        yield 2
    finally:
        print("finally ran")


print(list(guarded()))
