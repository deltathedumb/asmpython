# tier: spec
# ref: reference/datamodel.html#class-instances
# expect:
# 1
# ['a', 'injected']
# None
class C:
    pass

c = C()
c.__dict__["injected"] = 1
print(c.injected)
c.__dict__.update({"a": 2})
print(sorted(vars(c)))
print(C.__dict__.get("injected"))
