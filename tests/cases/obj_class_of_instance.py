# probes: __class__ and type() agree
# expect:
# Widget
# Widget
# True
class Widget:
    pass


w = Widget()
print(type(w).__name__)
print(w.__class__.__name__)
print(type(w) is Widget)
