import pytest

from app.pipeline import StageContext, StageStatus, run_stage
from app.recipes import REGISTRY
from app.recipes.fake import FakePrepareStage, FakePublishStage
from app.recipes.registry import RecipeDefinition, register


def test_register_rejects_a_recipe_with_an_invalid_lane():
    bad_recipe = RecipeDefinition(name="bogus", lane="quantum", stage_factories=[])

    with pytest.raises(ValueError):
        register(bad_recipe)

    assert "bogus" not in REGISTRY


def test_registry_contains_the_hidden_fake_recipe():
    recipe = REGISTRY["fake"]
    assert recipe.lane == "cpu"
    assert recipe.hidden is True
    stages = [factory({}) for factory in recipe.stage_factories]
    assert [stage.name for stage in stages] == ["fake_prepare", "fake_publish"]


def test_fake_prepare_stage_always_runs_even_when_overwrite_false(tmp_path):
    stage = FakePrepareStage(delay_seconds=0)
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    result = run_stage(stage, ctx)

    assert result.status == StageStatus.COMPLETED
    assert stage.declared_outputs(ctx) == []


def test_fake_publish_stage_writes_marker_file_atomically(tmp_path):
    source = tmp_path / "song.flac"
    stage = FakePublishStage(delay_seconds=0)
    ctx = StageContext(source_path=source, overwrite=False, options={})

    result = run_stage(stage, ctx)

    output = tmp_path / "song.fake.txt"
    assert result.status == StageStatus.COMPLETED
    assert output.exists()
    assert not (tmp_path / "song.fake.txt.part").exists()


def test_fake_publish_stage_is_skipped_when_output_exists_and_not_overwrite(tmp_path):
    source = tmp_path / "song.flac"
    (tmp_path / "song.fake.txt").write_text("already done", encoding="utf-8")
    stage = FakePublishStage(delay_seconds=0)
    ctx = StageContext(source_path=source, overwrite=False, options={})

    result = run_stage(stage, ctx)

    assert result.status == StageStatus.SKIPPED
    assert (tmp_path / "song.fake.txt").read_text(encoding="utf-8") == "already done"


def test_fake_publish_stage_overwrites_when_overwrite_true(tmp_path):
    source = tmp_path / "song.flac"
    (tmp_path / "song.fake.txt").write_text("stale", encoding="utf-8")
    stage = FakePublishStage(delay_seconds=0)
    ctx = StageContext(source_path=source, overwrite=True, options={})

    result = run_stage(stage, ctx)

    assert result.status == StageStatus.COMPLETED
    assert (tmp_path / "song.fake.txt").read_text(encoding="utf-8") != "stale"
