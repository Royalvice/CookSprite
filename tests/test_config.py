from __future__ import annotations

from cooksprite.config import read_config, resolve_data_dir, save_data_dir


def test_data_dir_has_one_stable_default(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("COOKSPRITE_CONFIG", str(tmp_path / ".cooksprite" / "config.toml"))
    monkeypatch.delenv("COOKSPRITE_DATA_DIR", raising=False)

    expected = (tmp_path / ".cooksprite" / "data").resolve()
    assert resolve_data_dir() == expected
    assert resolve_data_dir() == expected


def test_explicit_data_dir_is_persisted_for_every_later_start(monkeypatch, tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        'api_url = "http://127.0.0.1:9000"\nfuture_setting = "preserved"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("COOKSPRITE_CONFIG", str(config))
    monkeypatch.delenv("COOKSPRITE_DATA_DIR", raising=False)
    selected = tmp_path / "project-data"

    assert save_data_dir(selected) == selected.resolve()
    assert resolve_data_dir() == selected.resolve()
    saved = read_config()
    assert saved["api_url"] == "http://127.0.0.1:9000"
    assert saved["data_dir"] == str(selected.resolve())
    assert saved["future_setting"] == "preserved"


def test_data_dir_override_order_is_explicit_then_environment_then_config(
    monkeypatch, tmp_path
):
    config = tmp_path / "config.toml"
    config.write_text(f'data_dir = "{tmp_path / "configured"}"\n', encoding="utf-8")
    monkeypatch.setenv("COOKSPRITE_CONFIG", str(config))
    monkeypatch.setenv("COOKSPRITE_DATA_DIR", str(tmp_path / "environment"))

    assert resolve_data_dir() == (tmp_path / "environment").resolve()
    assert resolve_data_dir(tmp_path / "explicit") == (tmp_path / "explicit").resolve()
