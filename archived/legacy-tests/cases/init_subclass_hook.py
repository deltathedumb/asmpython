# expect:
# ['A', 'B']
class Base:
    subs = []
    def __init_subclass__(cls, **kw):
        Base.subs.append(cls.__name__)
class A(Base):
    pass
class B(Base):
    pass
print(Base.subs)
# asmpython (beta/3.14.0) MISMATCH: prints '[]\n' (wrong).
