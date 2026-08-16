# probes: an Enum may define its own methods
# expect:
# red=1
import enum


class Color(enum.Enum):
    RED = 1
    GREEN = 2

    def describe(self):
        return self.name.lower() + "=" + str(self.value)


print(Color.RED.describe())
