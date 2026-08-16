# probes: a metaclass __call__ wraps instantiation
# expect:
# 2
class Counting(type):
    made = 0

    def __call__(cls, *args, **kwargs):
        Counting.made = Counting.made + 1
        return super().__call__(*args, **kwargs)


class Widget(metaclass=Counting):
    pass


Widget()
Widget()
print(Counting.made)
