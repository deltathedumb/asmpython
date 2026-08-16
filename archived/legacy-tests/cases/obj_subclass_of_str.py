# probes: a str subclass keeps str behaviour
# expect:
# 3
# ADA
# ada!
class Name(str):
    def shout(self):
        return self.upper()


n = Name("ada")
print(len(n))
print(n.shout())
print(n + "!")
