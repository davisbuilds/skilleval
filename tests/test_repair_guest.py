"""Authored supervisor policy contracts, not proof of VM process isolation."""

from types import SimpleNamespace

import pytest

from skilleval import repair_guest


def test_fixed_dependency_paths_agree_with_native_solver_projection():
    from skilleval import solver_boundary

    assert repair_guest.NODE_DEPENDENCIES == solver_boundary.NODE_DEPENDENCIES
    assert repair_guest.NODE_DEPENDENCIES_TARGET == solver_boundary.NODE_DEPENDENCIES_TARGET


@pytest.mark.parametrize(
    "profile,address_space,file_size,open_files",
    [
        ("python-v1", 512 * 1024**2, 128 * 1024, 64),
        ("node-v1", 2 * 1024**3, 8 * 1024**2, 256),
    ],
)
def test_restrict_selects_named_profile_without_changing_process_or_core_limits(
    profile, address_space, file_size, open_files, monkeypatch
):
    chosen = {}
    monkeypatch.setattr(
        repair_guest.resource, "setrlimit", lambda key, value: chosen.update({key: value})
    )
    monkeypatch.setattr(
        repair_guest.ctypes, "CDLL", lambda *a, **kw: SimpleNamespace(prctl=lambda *args: 0)
    )
    repair_guest.restrict(profile)
    resource = repair_guest.resource
    assert chosen == {
        resource.RLIMIT_AS: (address_space, address_space),
        resource.RLIMIT_FSIZE: (file_size, file_size),
        resource.RLIMIT_NOFILE: (open_files, open_files),
        resource.RLIMIT_NPROC: (32, 32),
        resource.RLIMIT_CORE: (0, 0),
    }


def test_larger_temporary_file_budget_does_not_expand_retained_streams(tmp_path):
    stream = tmp_path / "stdout.bin"
    stream.write_bytes(b"x" * (repair_guest.LIMIT + 4096))
    retained, reached = repair_guest._read_output(stream)
    assert len(retained) == 128 * 1024
    assert reached is True
    stream.write_bytes(b"ordinary diagnostic")
    assert repair_guest._read_output(stream) == (b"ordinary diagnostic", False)


def test_unknown_guest_profile_is_rejected_before_any_limits_change(monkeypatch):
    called = []
    monkeypatch.setattr(repair_guest.resource, "setrlimit", lambda *args: called.append(args))
    with pytest.raises(ValueError, match="unsupported"):
        repair_guest.restrict("unbounded")
    assert called == []


@pytest.fixture
def dependency_layout(tmp_path, monkeypatch):
    image = tmp_path / "image-dependencies/node_modules"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    work = workspace / "task"
    work.mkdir()
    (work / "code.ts").write_text("ordinary candidate source\n")
    target = workspace / "node_modules"
    monkeypatch.setattr(repair_guest, "NODE_DEPENDENCIES", image)
    monkeypatch.setattr(repair_guest, "NODE_DEPENDENCIES_TARGET", target)
    return image, target, work


def test_image_dependencies_are_optional_and_fixed_projection_preserves_task_source(
    dependency_layout,
):
    image, target, work = dependency_layout
    assert repair_guest.project_image_dependencies(work) is False
    assert not target.exists()
    image.mkdir(parents=True)
    (image / "module.js").write_text("authored image module\n")
    assert repair_guest.project_image_dependencies(work) is True
    assert target.is_symlink()
    assert target.readlink() == image
    assert (target / "module.js").read_text() == "authored image module\n"
    assert (work / "code.ts").read_text() == "ordinary candidate source\n"
    assert repair_guest.project_image_dependencies(work) is True


@pytest.mark.parametrize("kind", ["directory", "file", "redirected", "dangling"])
def test_dependency_projection_rejects_unexpected_existing_aliases(dependency_layout, kind):
    image, target, work = dependency_layout
    image.mkdir(parents=True)
    if kind == "directory":
        target.mkdir()
    elif kind == "file":
        target.write_text("existing file\n")
    else:
        destination = target.parent / "untrusted"
        if kind == "redirected":
            destination.mkdir()
        target.symlink_to(destination)
    with pytest.raises(ValueError, match="alias"):
        repair_guest.project_image_dependencies(work)
    assert (work / "code.ts").read_text() == "ordinary candidate source\n"


@pytest.mark.parametrize("which", ["image", "workspace", "work"])
def test_dependency_projection_rejects_symlinked_ancestors(dependency_layout, which):
    image, target, work = dependency_layout
    image.mkdir(parents=True)
    original = {"image": image.parent, "workspace": target.parent, "work": work}[which]
    moved = original.with_name(original.name + "-moved")
    original.rename(moved)
    original.symlink_to(moved, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        repair_guest.project_image_dependencies(work)
    assert not target.exists()


def test_dependency_projection_rejects_source_overlap(dependency_layout, monkeypatch):
    image, _, work = dependency_layout
    image.mkdir(parents=True)
    monkeypatch.setattr(repair_guest, "NODE_DEPENDENCIES_TARGET", work / "node_modules")
    with pytest.raises(ValueError, match="overlap"):
        repair_guest.project_image_dependencies(work)
    assert not (work / "node_modules").exists()


@pytest.mark.parametrize("which", ["image", "workspace"])
def test_dependency_projection_rejects_writable_directory_ancestors(dependency_layout, which):
    image, target, work = dependency_layout
    image.mkdir(parents=True)
    unsafe = image.parent if which == "image" else target.parent
    unsafe.chmod(0o777)
    try:
        with pytest.raises(ValueError, match="writable or untrusted"):
            repair_guest.project_image_dependencies(work)
        assert not target.exists()
    finally:
        unsafe.chmod(0o755)
