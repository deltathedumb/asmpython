#!/bin/sh
# Build the mod jar. Drop the result in a NeoForge 1.21.1 `mods/` folder.
#
# Nothing here is a Minecraft feature of the compiler: these are the generic
# "shape a jar for a host that loads it" options (docs/JVM-HOSTED-JARS.md).
set -eu
cd "$(dirname "$0")"

asmpython build mod.py --backend jvm \
    --jvm-class demomod.DemoMod \
    --jvm-runtime-package demomod.rt \
    --jvm-instantiate "net.neoforged.bus.api.IEventBus" \
    --jvm-annotation "net.neoforged.fml.common.Mod(value=demomod)" \
    --jvm-resource "META-INF/neoforge.mods.toml=neoforge.mods.toml" \
    --jvm-resource "assets/demomod/models/item/bat.json=assets/bat.json" \
    --jvm-resource "assets/demomod/textures/item/bat.png=assets/bat.png" \
    --jvm-resource "assets/demomod/lang/en_us.json=assets/en_us.json" \
    -o demomod.jar

echo "wrote demomod.jar"
