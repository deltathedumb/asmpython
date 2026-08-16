# expect:
# 20
# val0 = 0
# val10 = 100
# val19 = 361
# contains12 = 1
# absent_key = 0
d = {}
d["k0"] = 0
d["k1"] = 1
d["k2"] = 4
d["k3"] = 9
d["k4"] = 16
d["k5"] = 25
d["k6"] = 36
d["k7"] = 49
d["k8"] = 64
d["k9"] = 81
d["k10"] = 100
d["k11"] = 121
d["k12"] = 144
d["k13"] = 169
d["k14"] = 196
d["k15"] = 225
d["k16"] = 256
d["k17"] = 289
d["k18"] = 324
d["k19"] = 361

print(len(d))
print("val0", "=", d["k0"])
print("val10", "=", d["k10"])
print("val19", "=", d["k19"])
print("contains12", "=", d.contains("k12"))
print("absent_key", "=", d.contains("k99"))
