# probes: an inner try handles before the outer sees it
# expect:
# inner handled
# done
try:
    try:
        raise ValueError("inner")
    except ValueError:
        print("inner handled")
except ValueError:
    print("outer handled")
print("done")
