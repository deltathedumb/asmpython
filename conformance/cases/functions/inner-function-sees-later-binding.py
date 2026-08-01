# tier: spec
# ref: reference/expressions.html#atom-identifiers
# expect:
# late
def outer():
    def inner():
        return value
    value = "late"
    return inner()

print(outer())
