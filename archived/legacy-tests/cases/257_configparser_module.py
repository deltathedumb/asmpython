# expect:
# 1
# hello
# 42
# 3.14
# 1
# 0
# 2
# 0

import configparser

cp = configparser.ConfigParser()
cp.add_section("main")
cp.set("main", "name", "hello")
cp.set("main", "count", "42")
cp.set("main", "pi", "3.14")
cp.set("main", "enabled", "true")
cp.set("main", "off", "false")

print(cp.has_section("main"))
print(cp.get("main", "name"))
print(cp.getint("main", "count"))
print(cp.getfloat("main", "pi"))
print(cp.getboolean("main", "enabled"))
print(cp.getboolean("main", "off"))

cp.add_section("extra")
secs: list = cp.sections()
print(len(secs))

cp.remove_option("main", "off")
opts: list = cp.options("main")
print(cp.has_option("main", "off"))
