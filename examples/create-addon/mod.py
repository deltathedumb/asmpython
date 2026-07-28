"""Extending Create (simibubi) from Python.

Create's boiler heat comes from a registry of Block -> BoilerHeater. Both are
public API, and BoilerHeater is a single-method INTERFACE -- which is the whole
reason this is reachable: compiled Python cannot subclass a Java class, but
`jproxy` can implement an interface.

So this makes magma blocks heat a Create steam boiler, at a heat level between
a passive source and a full blaze burner.
"""
import java
from asmpython import Public, access

MOD_ID = "createpy"

# Create's own scale: 0 = none, 1 = passive, and a blaze burner goes higher.
MAGMA_HEAT = 2.0


@access(Public)
def magma_heat(level, pos, state):
    """BoilerHeater.getHeat(Level, BlockPos, BlockState) -> float.

    Called by Create whenever a boiler looks below itself for heat. The
    arguments arrive as handles; this one needs none of them, because a magma
    block heats the same wherever it is.
    """
    return MAGMA_HEAT


@access(Public)
def on_construct(mod_bus):
    BoilerHeater = java.jclass("com.simibubi.create.api.boiler.BoilerHeater")
    registry = java.jfield(BoilerHeater, "REGISTRY")

    Blocks = java.jclass("net.minecraft.world.level.block.Blocks")
    magma = java.jfield(Blocks, "MAGMA_BLOCK")

    heater = java.jproxy("com.simibubi.create.api.boiler.BoilerHeater", "magma_heat")
    java.jcall_oo(registry, "register", magma, heater)

    # Read it back: proof the registration landed in Create's own registry
    # rather than merely not throwing.
    stored = java.jcall_o(registry, "get", magma)
    print("[" + MOD_ID + "] magma_block registered as a boiler heater: "
          + java.jcalls(java.jcall(stored, "getClass"), "getSimpleName"))
    return 0


def main():
    print("[" + MOD_ID + "] extending Create " + create_version())


def create_version():
    ModList = java.jclass("net.neoforged.fml.ModList")
    instance = java.jcall(ModList, "get")
    container = java.jcall_s(instance, "getModContainerById", "create")
    present = java.jcall(container, "isPresent")
    if present == 0:
        return "(not found)"
    mod = java.jcall(container, "get")
    info = java.jcall(mod, "getModInfo")
    return java.jcalls(java.jcall(info, "getVersion"), "toString")
