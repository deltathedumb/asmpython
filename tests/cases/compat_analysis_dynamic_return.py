# guards: analysis_compat_fixes
# expect:
# value=7
# value=x
def describe(value):
    return "value=" + str(value)


def unused_helper(payload):
    # Never called by the entry graph. Must not block compilation.
    return payload.missing_attribute_on_purpose()


print(describe(7))
print(describe("x"))
