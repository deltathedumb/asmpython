"""Distribution: what a build is wrapped in once it exists.

Package formats (.apx/.apb/.apl/.apext), signing, project manifests, and the
host-side installers. None of it is reachable from compilation -- the compiler
never imports this package -- which is the point of separating it: these
modules answer to registry formats and filesystem layout, not to codegen.
"""
