"""Legacy import shim for removed package path."""

raise ModuleNotFoundError(
    "RedstoneCompiler.Main was moved to Compiler.Main. Update entrypoints and scripts to use Compiler.Main"
)
