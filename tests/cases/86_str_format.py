# expect:
# a and b
# x y x
# count: 42
# 75% done
# hello world
# {literal} 1
print("{} and {}".format("a", "b"))
print("{0} {1} {0}".format("x", "y"))
print("count: {}".format(42))
print("{}% done".format(75))
name = "world"
print("hello {}".format(name))
print("{{literal}} {}".format(1))
