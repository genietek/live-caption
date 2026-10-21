import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from livecaption import catalog  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Every test gets its own settings and model folders, and no network."""
    monkeypatch.setenv("LIVECAPTION_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    monkeypatch.setattr(catalog, "LEGACY_MODEL_ROOTS", ())
    return tmp_path


def make_model(path: Path, model_id: str = "tiny", *, preprocessor: bool | None = None) -> Path:
    """Create a structurally valid (fake) CTranslate2 model folder."""
    info = catalog.get(model_id)
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.json").write_text("{}")
    (path / "tokenizer.json").write_text("{}")
    (path / "vocabulary.json").write_text("[]")
    if preprocessor if preprocessor is not None else info.needs_preprocessor_config:
        (path / "preprocessor_config.json").write_text("{}")
    with open(path / "model.bin", "wb") as f:
        f.truncate(info.params_m * 1_000_000)   # sparse file, no real disk use
    return path
