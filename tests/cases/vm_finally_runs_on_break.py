# probes: finally executes on the break path
# expect:
# body 1
# finally 1
# finally 2
# done
def f():
    for i in [1, 2, 3]:
        try:
            if i == 2:
                break
            print("body", i)
        finally:
            print("finally", i)
    return "done"


print(f())
