# expect:
# 42


class Frame:
    def __init__(self, value: int) -> None:
        self.value: int = value

    def to_dict(self):
        return {"value": self.value}


class Renderer:
    def build_frame(self):
        return Frame(42)


class Engine:
    def __init__(self, renderer) -> None:
        self.renderer = renderer

    def frame(self):
        result = self.renderer.build_frame()
        return result


engine = Engine(Renderer())
print(engine.frame().to_dict()["value"])
