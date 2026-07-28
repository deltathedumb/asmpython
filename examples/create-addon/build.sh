#!/bin/sh
# Build the Create addon. Drop the jar in a mods/ folder that also has
# Create 6.x for 1.21.1 (which bundles Flywheel, Ponder and Registrate).
set -eu
cd "$(dirname "$0")"

asmpython build mod.py --backend jvm \
    --jvm-class createpy.Mod \
    --jvm-runtime-package createpy.rt \
    --jvm-instantiate "net.neoforged.bus.api.IEventBus" \
    --jvm-annotation "net.neoforged.fml.common.Mod(value=createpy)" \
    --jvm-resource "META-INF/neoforge.mods.toml=neoforge.mods.toml" \
    -o createpy.jar

echo "wrote createpy.jar"
