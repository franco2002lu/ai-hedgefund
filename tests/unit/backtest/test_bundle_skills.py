"""Tests for the bundle_skills script — snapshots the live skills directory."""
from __future__ import annotations

import json

import pytest

from scripts import bundle_skills


class TestBundleSkills:
    def test_copies_skill_files_to_named_bundle(self, tmp_path, monkeypatch):
        # Create a fake live skills directory
        source = tmp_path / "source_skills"
        (source / "base").mkdir(parents=True)
        (source / "base" / "fundamentals.md").write_text("# Fund")
        (source / "output_format.md").write_text("## Output")
        bundles_dir = tmp_path / "bundles"

        result_path = bundle_skills.create_bundle(
            name="baseline_v1",
            source_dir=source,
            bundles_dir=bundles_dir,
        )

        assert result_path == bundles_dir / "baseline_v1"
        assert (result_path / "base" / "fundamentals.md").read_text() == "# Fund"
        assert (result_path / "output_format.md").read_text() == "## Output"

    def test_refuses_overwrite_without_force(self, tmp_path):
        source = tmp_path / "source_skills"
        source.mkdir()
        (source / "x.md").write_text("x")
        bundles_dir = tmp_path / "bundles"

        bundle_skills.create_bundle("a", source, bundles_dir)
        with pytest.raises(FileExistsError, match="already exists"):
            bundle_skills.create_bundle("a", source, bundles_dir)

    def test_force_overwrites_existing_bundle(self, tmp_path):
        source = tmp_path / "source_skills"
        source.mkdir()
        (source / "x.md").write_text("first")
        bundles_dir = tmp_path / "bundles"

        bundle_skills.create_bundle("a", source, bundles_dir)
        (source / "x.md").write_text("second")
        bundle_skills.create_bundle("a", source, bundles_dir, force=True)

        assert (bundles_dir / "a" / "x.md").read_text() == "second"

    def test_writes_bundle_meta_json(self, tmp_path):
        source = tmp_path / "source_skills"
        source.mkdir()
        (source / "x.md").write_text("x")
        bundles_dir = tmp_path / "bundles"

        bundle_skills.create_bundle("a", source, bundles_dir)
        meta_path = bundles_dir / "a" / ".bundle_meta.json"
        assert meta_path.is_file()
        meta = json.loads(meta_path.read_text())
        assert "created_at" in meta
        assert "source_dir" in meta

    def test_excludes_pycache(self, tmp_path):
        source = tmp_path / "source_skills"
        (source / "base").mkdir(parents=True)
        (source / "base" / "fundamentals.md").write_text("# Fund")
        (source / "__pycache__").mkdir()
        (source / "__pycache__" / "x.pyc").write_bytes(b"compiled")
        bundles_dir = tmp_path / "bundles"

        bundle_skills.create_bundle("a", source, bundles_dir)
        assert not (bundles_dir / "a" / "__pycache__").exists()
