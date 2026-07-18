# expect-error: declarations require the 'interface' extension

interface Shape:
    def area(self) -> int:
        pass

print(1)
