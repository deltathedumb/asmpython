# probes: global rebinds a module-level name
# expect:
# 2
counter = 0


def bump():
    global counter
    counter = counter + 1


bump()
bump()
print(counter)
