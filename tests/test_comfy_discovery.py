from pathlib import Path

from cooksprite.comfy import discovery


def test_discovery_uses_the_process_serving_the_explicit_loopback_port(tmp_path, monkeypatch):
    root = tmp_path / "ComfyUI"
    (root / "comfy").mkdir(parents=True)
    (root / "main.py").write_text("", encoding="utf-8")
    (root / "nodes.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(discovery, "_listening_pids", lambda _port: ["123"])
    monkeypatch.setattr(discovery, "_command", lambda _pid: "python main.py --listen")
    monkeypatch.setattr(discovery, "_cwd", lambda _pid: Path(root))

    assert discovery.discover_comfy_directory("http://127.0.0.1:8188") == str(root)
