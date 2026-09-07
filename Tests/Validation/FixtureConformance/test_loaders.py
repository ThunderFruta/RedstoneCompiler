"""Source binding and ordinary data-only case discovery."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shutil

import pytest

from Validation.FixtureConformance.Expectations import FixtureConfigurationError
from Validation.FixtureConformance.Loaders import LoadFixture


Root = Path(__file__).resolve().parents[2] / "Fixtures" / "PhysicalRulesBatch1"


def test_both_source_formats_load_literal_blocks_and_hash_actual_source():
    Template = LoadFixture(Root / "Identity" / "Fixture.json")
    Litematic = LoadFixture(Root / "IdentityLitematic" / "Fixture.json")
    assert Template.Document == Litematic.Document
    assert Template.Sha256 == Litematic.Sha256
    assert Template.SourceHashes != Litematic.SourceHashes
    States = {tuple(B["Position"]): B["State"]["Name"] for B in Litematic.Document["Blocks"]}
    assert States == {(0, 0, 0): "minecraft:stone", (1, 0, 0): "minecraft:stone", (2, 0, 0): "minecraft:stone", (0, 1, 0): "minecraft:lever", (1, 1, 0): "minecraft:redstone_wire", (2, 1, 0): "minecraft:redstone_wire"}


def test_changed_source_cannot_retain_original_fixture_identity(tmp_path):
    shutil.copytree(Root / "Identity", tmp_path / "Case")
    Template = tmp_path / "Case" / "Template.json"
    Template.write_text(Template.read_text() + "\n")
    with pytest.raises(FixtureConfigurationError, match="stale-fixture-source"):
        LoadFixture(tmp_path / "Case" / "Fixture.json")


def test_duplicate_blocks_and_control_positions_are_configuration_failures(tmp_path):
    shutil.copytree(Root / "Identity", tmp_path / "Case")
    P = tmp_path / "Case" / "Fixture.json"
    T = P.with_name("Template.json")
    Blocks = json.loads(T.read_text())
    Blocks["Blocks"].append(deepcopy(Blocks["Blocks"][0]))
    T.write_text(json.dumps(Blocks))
    D = json.loads(P.read_text())
    D["source"]["sha256"] = sha256(T.read_bytes()).hexdigest()
    P.write_text(json.dumps(D))
    with pytest.raises(FixtureConfigurationError, match="duplicate-block-position"):
        LoadFixture(P)


def test_configuration_failures_retain_original_inputs_before_validation(tmp_path):
    from Validation.FixtureConformance.Runner import RunFixtures
    Fixtures = tmp_path / "Fixtures"
    for Name in ("Malformed", "Stale"):
        shutil.copytree(Root / "Identity", Fixtures / Name)
    (Fixtures / "Malformed" / "Expectations.json").write_text('{"broken":')
    Template = Fixtures / "Stale" / "Template.json"
    Template.write_text(Template.read_text() + "\n")
    Report = RunFixtures(Fixtures, tmp_path / "Evidence")
    assert [R["Status"] for R in Report["Results"]] == ["configuration-error", "configuration-error"]
    for Index, Name in enumerate(("Malformed", "Stale")):
        Evidence = tmp_path / "Evidence" / f"fixture-{Index:04d}"
        Receipt = json.loads((Evidence / "Sources.json").read_text())
        assert {Path(R["Path"]).name for R in Receipt["Sources"]} == {"Fixture.json", "Template.json", "Expectations.json"}
        for Record in Receipt["Sources"]:
            Content = (Evidence / Record["Copy"]).read_bytes()
            assert Content == Path(Record["Path"]).read_bytes()
            assert Record["Sha256"] == sha256(Content).hexdigest()
        assert (Evidence / "Failure.json").is_file()


def test_invalid_repeater_state_fails_full_harness_with_retained_input(tmp_path):
    from Validation.FixtureConformance.Runner import RunFixtures
    FixturePath = tmp_path / "Cases" / "Invalid"
    shutil.copytree(Root / "Directional", FixturePath)
    TemplatePath = FixturePath / "Template.json"
    Template = json.loads(TemplatePath.read_text())
    for B in Template["Blocks"]:
        if B["State"]["Name"] == "minecraft:repeater":
            B["State"]["Properties"]["delay"] = "0"
    TemplatePath.write_text(json.dumps(Template))
    Descriptor = json.loads((FixturePath / "Fixture.json").read_text())
    Descriptor["source"]["sha256"] = sha256(TemplatePath.read_bytes()).hexdigest()
    (FixturePath / "Fixture.json").write_text(json.dumps(Descriptor))
    Report = RunFixtures(tmp_path / "Cases", tmp_path / "Evidence")
    assert Report["Results"][0]["Status"] == "configuration-error"
    assert "repeater-delay" in Report["Results"][0]["Reason"]
    assert (tmp_path / "Evidence" / "fixture-0000" / "Failure.json").is_file()
