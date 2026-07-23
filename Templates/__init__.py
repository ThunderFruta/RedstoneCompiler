"""Template catalog using local litematic template pack."""

from pathlib import Path

ExternalTemplateDirectory = Path(
    "/home/bananawewe/Documents/curseforge/minecraft/Instances/wee/schematics"
)
RequiredTemplateNames = ("Input.litematic", "Output.litematic", "Nand.litematic")
TemplateDirectory = (
    ExternalTemplateDirectory
    if all(
        (ExternalTemplateDirectory / Name).is_file()
        for Name in RequiredTemplateNames
    )
    else Path(__file__).resolve().parent
)

LitematicTemplates = {
    "Input": TemplateDirectory / "Input.litematic",
    "Output": TemplateDirectory / "Output.litematic",
    "Nand": TemplateDirectory / "Nand.litematic",
}
