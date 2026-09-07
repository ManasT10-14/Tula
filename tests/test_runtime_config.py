"""Source and installed administration commands share the web runtime location."""
import io
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tula import config
from tula.security import __main__ as security_cli


def test_explicit_runtime_path_is_absolute_and_cwd_independent(tmp_path, monkeypatch):
    chosen = tmp_path / "configured data"
    monkeypatch.setenv("TULA_DATA_DIR", str(chosen))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert config.runtime_root() == chosen.resolve()


def test_relative_override_is_resolved_against_launch_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TULA_DATA_DIR", "runtime")
    assert config.runtime_root() == tmp_path / "runtime"


@pytest.mark.parametrize("layout", ["source", "installed"])
def test_default_runtime_source_vs_installed(layout, tmp_path, monkeypatch):
    monkeypatch.delenv("TULA_DATA_DIR", raising=False)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "pyproject.toml").write_text("[project]\nname='test'\n")
    home = tmp_path / "user-home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    module = (checkout / "src/tula/config.py" if layout == "source"
              else tmp_path / "wheel/Lib/site-packages/tula/config.py")
    monkeypatch.setattr(config, "__file__", str(module))
    assert config.runtime_root() == (checkout if layout == "source" else home / ".tula")


@pytest.mark.parametrize("layout", ["source", "installed", "explicit"])
def test_security_bootstrap_uses_shared_runtime_not_the_shell_directory(layout, tmp_path, monkeypatch, capsys):
    home, checkout = tmp_path / "home", tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "pyproject.toml").write_text("[project]\nname='test'\n")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("TULA_DATA_DIR", raising=False)
    monkeypatch.setattr(config, "__file__", str(checkout / "src/tula/config.py" if layout == "source"
                        else tmp_path / "wheel/Lib/site-packages/tula/config.py"))
    if layout == "explicit":
        monkeypatch.setenv("TULA_DATA_DIR", str(tmp_path / "configured"))
    monkeypatch.chdir(tmp_path)
    expected = config.runtime_root() / "data/tula.db"
    captured = []

    class FakeStore:
        def __init__(self, path):
            self.path = path
            captured.append(path)

        def has_users(self):
            return False

        def create_user(self, username, password, **kwargs):
            assert password == "Temporary isolated test password!"
            return SimpleNamespace(username=username)

    monkeypatch.setattr(security_cli, "SecurityStore", FakeStore)
    monkeypatch.setattr(security_cli.sys, "stdin", io.StringIO("Temporary isolated test password!\n"))
    assert security_cli.main(["bootstrap", "--username", "isolated.admin", "--password-stdin"]) == 0
    assert captured == [expected]
    assert "Temporary isolated test password!" not in capsys.readouterr().out


def test_home_override_expands_tilde(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TULA_DATA_DIR", "~/tula-test")
    assert config.runtime_root() == tmp_path / "tula-test"


def test_new_server_process_cannot_initialize_database_during_maintenance(tmp_path):
    from tula.storage.backup import runtime_lease

    root = tmp_path / "runtime-not-yet-created"
    environment = {**os.environ, "TULA_DATA_DIR": str(root),
                   "PYTHONPATH": str(Path(config.__file__).resolve().parents[1])}
    with runtime_lease(root, exclusive=True):
        result = subprocess.run([sys.executable, "-c", "import tula.web.app"],
                                env=environment, cwd=tmp_path, capture_output=True, text=True,
                                timeout=30, check=False)
    assert result.returncode != 0
    assert "Runtime is active or maintenance is in progress" in result.stderr
    assert not root.exists()


def test_first_server_lease_supports_nested_runtime_with_missing_parent(tmp_path):
    from tula.storage.backup import runtime_lease

    root = tmp_path / "new-parent/nested/runtime"
    assert not root.parent.exists()
    with runtime_lease(root):
        assert root.parent.is_dir()
        assert not (root / "data/tula.db").exists()
        with pytest.raises(ValueError, match="Runtime is active"), runtime_lease(root, exclusive=True):
            pytest.fail("Maintenance must remain excluded during first startup")
