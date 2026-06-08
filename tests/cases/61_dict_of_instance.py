# expect:
# alice -> 30
# bob -> 25
# missing? 0
# updated alice -> 31

class Person:
    def __init__(self, age):
        self.age = age

    def get_age(self):
        return self.age


# A dict whose values are class instances (the symbol-table pattern).
people = {"alice": Person(30), "bob": Person(25)}

print("alice ->", people["alice"].get_age())
print("bob ->", people["bob"].get_age())

# Use .get with a sentinel of the right type — int default isn't quite
# right for a dict[str, Person], so we just probe via .contains.
print("missing?", int(people.contains("carol")))

# Mutate the value in place via attribute write.
people["alice"].age = 31
print("updated alice ->", people["alice"].get_age())
