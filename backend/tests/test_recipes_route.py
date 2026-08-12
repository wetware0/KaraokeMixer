from fastapi.testclient import TestClient

from app.main import create_app


def test_get_recipes_excludes_hidden_recipes_and_includes_options_schema(tmp_path):
    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)

    response = client.get("/api/recipes")

    assert response.status_code == 200
    recipes = {recipe["name"]: recipe for recipe in response.json()["recipes"]}
    assert "fake" not in recipes  # hidden
    assert "align_only" not in recipes  # editor-only hidden command
    assert recipes["karaoke"]["lane"] == "gpu"
    assert recipes["karaoke"]["options_schema"]["backing_vocal_mode"]["choices"] == [
        "stripped", "faint", "stereo_mix", "best",
    ]
    assert recipes["full_stems"]["options_schema"]["model"]["default"] == "htdemucs"
    assert "fetch_tags" in recipes
    assert recipes["fetch_tags"]["lane"] == "cpu"
    assert recipes["fetch_tags"]["options_schema"] is None
    assert "full_prep" in recipes
    assert recipes["full_prep"]["lane"] == "gpu"
    assert recipes["full_prep"]["options_schema"]["model"]["default"] == "htdemucs"
