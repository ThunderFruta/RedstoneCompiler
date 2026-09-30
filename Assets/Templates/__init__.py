"""Template catalog using local litematic template pack."""

import os
from pathlib import Path

ExternalTemplateDirectory = Path(
    "/home/bananawewe/.local/share/PrismLauncher/instances/wee 26.2/minecraft/schematics"
)
RequiredTemplateNames = ("Input.litematic", "Output.litematic", "Nand.litematic")
_ExplicitTemplateRoot = os.environ.get("RC_TEMPLATE_ROOT")
TemplateDirectory = (
    Path(_ExplicitTemplateRoot).expanduser().resolve()
    if _ExplicitTemplateRoot is not None
    else (
        ExternalTemplateDirectory
        if all(
            (ExternalTemplateDirectory / Name).is_file()
            for Name in RequiredTemplateNames
        )
        else Path(__file__).resolve().parent
    )
)
if _ExplicitTemplateRoot is not None and (
    not _ExplicitTemplateRoot.strip()
    or not all((TemplateDirectory / Name).is_file() for Name in RequiredTemplateNames)
):
    raise ValueError("RC_TEMPLATE_ROOT must contain all three required litematic templates")

LitematicTemplates = {
    "Input": TemplateDirectory / "Input.litematic",
    "Output": TemplateDirectory / "Output.litematic",
    "Nand": TemplateDirectory / "Nand.litematic",
}
