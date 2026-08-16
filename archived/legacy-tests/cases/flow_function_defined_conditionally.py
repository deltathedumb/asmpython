# probes: a def inside a branch binds only when run
# expect:
# yes
# no
def pick(flag):
    if flag:
        def impl():
            return "yes"
    else:
        def impl():
            return "no"
    return impl()


print(pick(True))
print(pick(False))
