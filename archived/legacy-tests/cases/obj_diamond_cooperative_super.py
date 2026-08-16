# probes: diamond super() visits each class once
# expect:
# ['D', 'B', 'C', 'A']
class A:
    def run(self):
        return ["A"]


class B(A):
    def run(self):
        return ["B"] + super().run()


class C(A):
    def run(self):
        return ["C"] + super().run()


class D(B, C):
    def run(self):
        return ["D"] + super().run()


print(D().run())
