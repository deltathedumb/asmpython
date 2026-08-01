# probes: a closure reaches two scopes out
# expect:
# one-two
def level1():
    a = "one"

    def level2():
        b = "two"

        def level3():
            return a + "-" + b

        return level3()

    return level2()


print(level1())
