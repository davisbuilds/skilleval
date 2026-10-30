"""Authored mechanical controls do not test semantic quality."""

import importlib.util
from pathlib import Path


def test_distinct_valid_drafts_and_semantic_falsehood_share_format(tmp_path):
    example = Path(__file__).resolve().parents[1] / "examples/document/product-page"
    spec = importlib.util.spec_from_file_location("authored_document_worker", example / "worker.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("search-first.md", "activity-first.md"):
        text = (example / "controls" / name).read_text()
        (tmp_path / "page.md").write_text(text)
        assert all(module.observe(tmp_path).values())
    falsehood = text.replace(
        "Cost figures are estimates and exclude unpriced models.",
        "Cost figures are exact and include every model without exception.",
    )
    assert falsehood != text
    (tmp_path / "page.md").write_text(falsehood)
    assert all(module.observe(tmp_path).values())
    (tmp_path / "page.md").write_text(falsehood.replace("#quick-start", "#signup"))
    assert module.observe(tmp_path)["cta"] is False
