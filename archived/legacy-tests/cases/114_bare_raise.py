# expect:
# caught: boom
# outer: boom
# reraise: No active exception to reraise

def f():
    try:
        raise ValueError("boom")
    except ValueError as e:
        print("caught:", e)
        raise

try:
    f()
except ValueError as e:
    print("outer:", e)

try:
    raise
except RuntimeError as e:
    print("reraise:", e)
