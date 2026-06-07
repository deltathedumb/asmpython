import math

GLOBAL_VAR = 123


class TestClass:
    class_var = "class_value"

    def __init__(self, value):
        self.value = value

    def multiply(self, x):
        return self.value * x


def fibonacci(n):
    a, b = 0, 1
    result = []
    for _ in range(n):
        result.append(a)
        a, b = b, a + b
    return result


def generator_test():
    for i in range(5):
        yield i * i


def exception_test():
    try:
        return 1 / 0
    except ZeroDivisionError:
        return "caught"


def recursive_factorial(n):
    if n <= 1:
        return 1
    return n * recursive_factorial(n - 1)


def main():
    print("=== BASIC TYPES ===")
    print(42)
    print(3.14159)
    print(True)
    print("hello world")

    print("\n=== ARITHMETIC ===")
    print(10 + 5)
    print(10 - 5)
    print(10 * 5)
    print(10 / 5)
    print(10 % 3)
    print(2**8)

    print("\n=== LISTS ===")
    nums = [1, 2, 3]
    nums.append(4)
    print(nums)
    print(nums[2])

    print("\n=== DICTS ===")
    d = {"a": 1, "b": 2}
    print(d["a"])

    print("\n=== LOOPS ===")
    total = 0
    for i in range(10):
        total += i
    print(total)

    i = 0
    while i < 3:
        print(i)
        i += 1

    print("\n=== FUNCTIONS ===")
    print(fibonacci(10))

    print("\n=== RECURSION ===")
    print(recursive_factorial(6))

    print("\n=== CLASSES ===")
    obj = TestClass(7)
    print(obj.multiply(6))
    print(TestClass.class_var)

    print("\n=== GENERATORS ===")
    print(list(generator_test()))

    print("\n=== EXCEPTIONS ===")
    print(exception_test())

    print("\n=== COMPREHENSIONS ===")
    print([x * x for x in range(5)])

    print("\n=== TUPLES ===")
    t = (1, 2, 3)
    a, b, c = t
    print(a, b, c)

    print("\n=== BOOLEAN LOGIC ===")
    print(True and False)
    print(True or False)
    print(not True)

    print("\n=== BUILTINS ===")
    print(len("abcdef"))
    print(sum([1, 2, 3, 4, 5]))
    print(max([1, 9, 3]))

    print("\n=== MATH MODULE ===")
    print(round(math.sqrt(144)))
    print(round(math.sin(1), 6))

    print("\n=== GLOBALS ===")
    print(GLOBAL_VAR)

    print("\n=== STRING FORMATTING ===")
    name = "Compiler"
    print(f"Hello {name}")

    print("\n=== DONE ===")


if __name__ == "__main__":
    main()
