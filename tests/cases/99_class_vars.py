# expect:
# 5
# app
# 3.14
# 10
# 8
class Config:
    version = 5
    name = "app"
    pi = 3.14
print(Config.version)
print(Config.name)
print(Config.pi)
Config.version = 10
print(Config.version)
class Counter:
    total = 0
Counter.total += 5
Counter.total += 3
print(Counter.total)
