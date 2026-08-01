# probes: finally executes on the return path
# expect:
# finally-ran
# from-try
def f():
    try:
        return "from-try"
    finally:
        print("finally-ran")


print(f())
