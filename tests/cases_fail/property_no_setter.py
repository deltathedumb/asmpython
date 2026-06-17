# expect-error: property 'area' of 'Box' object has no setter


class Box:
    def __init__(self, w: int, h: int) -> None:
        self.w = w
        self.h = h

    @property
    def area(self) -> int:
        return self.w * self.h


b = Box(3, 4)
b.area = 99
