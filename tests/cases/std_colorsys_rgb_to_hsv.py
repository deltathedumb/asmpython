# probes: colorsys converts RGB to HSV
# expect:
# (0.0, 1.0, 1.0)
import colorsys

print(colorsys.rgb_to_hsv(1.0, 0.0, 0.0))
