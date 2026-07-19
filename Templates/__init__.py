"""Template catalog using local litematic template pack."""

from pathlib import Path

TemplateDirectory = (
    Path("/home/bananawewe/Documents/curseforge/minecraft/Instances/wee/schematics")
    if Path("/home/bananawewe/Documents/curseforge/minecraft/Instances/wee/schematics").exists()
    else Path(__file__).resolve().parent
)

LitematicTemplates = {
    "Input": TemplateDirectory / "Input.litematic",
    "Output": TemplateDirectory / "Output.litematic",
    "Nand": TemplateDirectory / "Nand.litematic",
}
