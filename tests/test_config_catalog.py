import json

import pytest

from livecaption import catalog, config

from .conftest import make_model


# -- settings ------------------------------------------------------------------

def test_settings_round_trip():
    s = config.Settings(language="es", device="cpu", chunk_seconds=2.5, auto_type=True,
                        imported_models={"small": r"C:\models\small"})
    config.save_settings(s)
    loaded = config.load_settings()
    assert loaded == s


def test_settings_defaults_are_safe():
    s = config.load_settings()
    assert s.auto_type is False          # typing into other apps is opt-in
    assert s.auto_download is False      # no download without consent
    assert s.start_with_windows is False
    assert s.global_hotkeys is False
    assert s.resolved_model_dir() == config.default_model_dir()


def test_corrupt_settings_fall_back_to_defaults():
    path = config.settings_path()
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    assert config.load_settings() == config.Settings()
    assert path.with_suffix(".json.bad").exists()


def test_invalid_values_are_ignored():
    path = config.settings_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"language": "fr", "device": 3, "beam_size": True,
                                "chunk_seconds": 1.0, "overlap_seconds": 2.0, "unknown": 1}))
    s = config.load_settings()
    assert s.language == "auto" and s.device == "auto" and s.beam_size == 1
    assert s.overlap_seconds < s.chunk_seconds


def test_settings_paths_are_separate(monkeypatch, tmp_path):
    monkeypatch.delenv("LIVECAPTION_HOME")
    monkeypatch.setattr(config.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert config.settings_path() == tmp_path / "Roaming" / "LiveCaption" / "settings.json"
    assert config.default_model_dir() == tmp_path / "Local" / "LiveCaption" / "models"


# -- catalog ---------------------------------------------------------------------

def test_catalog_matches_faster_whisper():
    fw_utils = pytest.importorskip("faster_whisper.utils")
    ids = [m.id for m in catalog.CATALOG]
    assert ids == ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]
    for m in catalog.CATALOG:
        assert fw_utils._MODELS[m.id] == m.repo
    assert fw_utils._MODELS["turbo"] == catalog.get("turbo").repo


def test_catalog_has_no_english_only_models():
    for m in catalog.CATALOG:
        assert ".en" not in m.id and "distil" not in m.repo
        assert m.description and m.speed and m.accuracy


def test_validate_complete_model(tmp_path):
    ok, reason = catalog.validate_model_dir(make_model(tmp_path / "tiny"), catalog.get("tiny"))
    assert ok, reason


@pytest.mark.parametrize("breakage, expected", [
    (lambda p: (p / "model.bin").unlink(), "Missing model.bin"),
    (lambda p: (p / "tokenizer.json").write_text(""), "Missing tokenizer.json"),
    (lambda p: (p / "vocabulary.json").unlink(), "Missing vocabulary"),
    (lambda p: (p / "config.json").write_text("{oops"), "config.json"),
    (lambda p: open(p / "model.bin", "r+b").truncate(1000), "incomplete"),
])
def test_validate_rejects_broken_models(tmp_path, breakage, expected):
    path = make_model(tmp_path / "tiny")
    breakage(path)
    ok, reason = catalog.validate_model_dir(path, catalog.get("tiny"))
    assert not ok and expected in reason


def test_empty_directory_is_not_installed(tmp_path):
    (tmp_path / "models" / "small").mkdir(parents=True)
    result = catalog.scan(tmp_path / "models")
    assert "small" not in result.installed
    assert "small" in result.invalid


def test_large_models_need_preprocessor_config(tmp_path):
    path = make_model(tmp_path / "turbo", "large-v3-turbo", preprocessor=False)
    ok, reason = catalog.validate_model_dir(path, catalog.get("large-v3-turbo"))
    assert not ok and "preprocessor" in reason


def test_manifest_size_mismatch_is_rejected(tmp_path):
    path = make_model(tmp_path / "tiny")
    (path / catalog.MANIFEST_NAME).write_text(json.dumps(
        {"model_id": "tiny", "files": {"config.json": {"size": 999}}}))
    ok, reason = catalog.validate_model_dir(path, catalog.get("tiny"))
    assert not ok and "wrong size" in reason


def test_scan_finds_managed_and_imported(tmp_path):
    models = tmp_path / "models"
    make_model(models / "tiny")
    ext = make_model(tmp_path / "elsewhere" / "base", "base")
    result = catalog.scan(models, {"base": str(ext), "small": str(tmp_path / "missing")})
    assert set(result.installed) == {"tiny", "base"}
    assert result.installed["tiny"].managed and not result.installed["base"].managed
    assert "small" in result.invalid


def test_find_existing_hf_cache_snapshots(tmp_path):
    root = tmp_path / "hub"
    snap = make_model(root / "models--Systran--faster-whisper-small" / "snapshots" / "abc123", "small")
    broken = root / "models--Systran--faster-whisper-base" / "snapshots" / "def456"
    broken.mkdir(parents=True)
    (root / "models--Systran--faster-distil-whisper-large-v3").mkdir()
    found, notes = catalog.find_existing_models([root, tmp_path / "does-not-exist"])
    assert found == {"small": snap}            # reused in place, not copied
    assert any("distil" in n for n in notes)   # English-only model reported, not offered


def test_delete_managed_model(tmp_path):
    models = tmp_path / "models"
    make_model(models / "tiny")
    catalog.delete_managed_model(models, "tiny")
    assert not (models / "tiny").exists()
    assert "tiny" not in catalog.scan(models).installed


@pytest.mark.parametrize("gpu, vram, ram, cores, expected", [
    (True, 8192, 32, 16, "large-v3-turbo"),
    (True, 2048, 16, 8, "small"),
    (False, None, 16, 8, "small"),
    (False, None, 8, 4, "base"),
    (False, None, 2, 2, "tiny"),
])
def test_recommendation(gpu, vram, ram, cores, expected):
    assert catalog.recommend(gpu, vram, ram, cores) == expected
