# probes: configparser parses an INI document
# expect:
# ada
# 2
import configparser

parser = configparser.ConfigParser()
parser.read_string("[main]\nname = ada\ncount = 2\n")
print(parser["main"]["name"])
print(parser.getint("main", "count"))
