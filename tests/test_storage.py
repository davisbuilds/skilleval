import json
import stat
from pathlib import Path

import pytest

from skilleval import cli
from skilleval.errors import SkillEvalError
from skilleval.storage import GROUPS, data_root, initialize, storage_path


def test_data_root_is_independent_of_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "private-store"
    monkeypatch.setenv("SKILLEVAL_DATA_HOME", str(target))
    first = tmp_path / "checkout-one"
    second = tmp_path / "checkout-two"
    first.mkdir()
    second.mkdir()
    monkeypatch.chdir(first)
    assert data_root() == target
    monkeypatch.chdir(second)
    assert data_root() == target
    assert not target.exists()


def test_explicit_root_wins_and_relative_settings_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILLEVAL_DATA_HOME", "relative")
    assert data_root(tmp_path) == tmp_path
    with pytest.raises(SkillEvalError, match="absolute"):
        data_root()


def test_platform_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SKILLEVAL_DATA_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("skilleval.storage.sys.platform", "darwin")
    assert data_root() == tmp_path / "Library/Application Support/skilleval"
    monkeypatch.setattr("skilleval.storage.sys.platform", "linux")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert data_root() == tmp_path / ".local/share/skilleval"
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert data_root() == tmp_path / "xdg/skilleval"


def test_private_store_is_idempotent_and_outputs_remain_unreserved(tmp_path: Path) -> None:
    root = tmp_path / "store"
    first = initialize(root)
    assert first == initialize(root)
    for path in (root, *(root / group for group in GROUPS)):
        assert stat.S_IMODE(path.stat().st_mode) == 0o700
    target = Path(storage_path("experiments", "debugging-v1", root=root)["path"])
    assert target == root / "experiments/debugging-v1"
    assert not target.exists()
    # Existing artifacts survive subsequent initialization.
    target.mkdir()
    (target / "receipt.json").write_text("retained")
    initialize(root)
    assert (target / "receipt.json").read_text() == "retained"


def test_unrelated_nonempty_store_and_git_roots_are_not_adopted(tmp_path: Path) -> None:
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "keep").write_text("untouched")
    with pytest.raises(SkillEvalError, match="nonempty"):
        initialize(occupied)
    assert sorted(path.name for path in occupied.iterdir()) == ["keep"]
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".git").write_text("gitdir: somewhere")
    with pytest.raises(SkillEvalError, match="outside a Git"):
        initialize(checkout / "data")
    assert not (checkout / "data").exists()


def test_initialization_refuses_symlink_ancestors_and_groups(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(OSError):
        initialize(link / "store")
    assert not (real / "store").exists()
    root = tmp_path / "store"
    initialize(root)
    (root / "images").rmdir()
    (root / "images").symlink_to(real, target_is_directory=True)
    with pytest.raises(OSError):
        initialize(root)
    assert list(real.iterdir()) == []


@pytest.mark.parametrize("name", ["..", "../outside", "/absolute", "", "x" * 129])
def test_path_names_cannot_escape_group(tmp_path: Path, name: str) -> None:
    with pytest.raises(SkillEvalError, match=r"single|component"):
        storage_path("experiments", name, root=tmp_path)


def test_cli_path_and_init(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "store with spaces"
    args = ["storage", "path", "experiments", "first-run", "--root", str(root)]
    assert cli.main([*args, "--plain"]) == 0
    assert capsys.readouterr().out.rstrip("\n") == str(root / "experiments/first-run")
    assert not root.exists()
    assert cli.main(["storage", "init", "--root", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ready"
    assert cli.main(args) == 0
    assert json.loads(capsys.readouterr().out)["path"] == str(root / "experiments/first-run")


def test_cli_staging_preview_does_not_create_or_delete_data(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing"
    assert cli.main(["storage", "prune-staging", str(missing)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["kind"] == "skilleval-staging-retention"
    assert not missing.exists()
