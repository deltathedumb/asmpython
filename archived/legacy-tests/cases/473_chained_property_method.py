# expect:
# 1

class Object:
    @property
    def type_name(self):
        return "somnia.Object"


print(Object().type_name.startswith("somnia."))
