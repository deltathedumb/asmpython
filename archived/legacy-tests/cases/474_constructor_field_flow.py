# expect:
# 42
# 2


class Model:
    def value(self) -> int:
        return 42


class Renderer:
    def frame(self) -> int:
        return 2


class Engine:
    def __init__(self, model=None, renderer=None) -> None:
        self.model = model or Model()
        self.renderer = renderer or Renderer()

    def run(self) -> int:
        return self.model.value()

    def frames(self) -> int:
        return self.renderer.frame()


engine = Engine()
print(engine.run())
print(engine.frames())
