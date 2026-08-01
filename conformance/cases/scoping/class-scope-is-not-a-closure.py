# tier: spec
# ref: reference/executionmodel.html#resolution-of-names
# expect:
# module
# class
value = "module"

class C:
    value = "class"
    def method(self):
        return value

print(C().method())
print(C.value)
