"""Snapshot the live analyst skills directory as a named bundle.

Usage:
    python -m scripts.bundle_skills <name> [--force]

Copies app/modules/equities/agents/skills/ (excluding __pycache__) to
data/skill_bundles/<name>/. A .bundle_meta.json file is written alongside
with creation timestamp and source directory for provenance.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_DEFAULT_SOURCE = Path("app/modules/equities/agents/skills")
_DEFAULT_BUNDLES_DIR = Path("data/skill_bundles")
_EXCLUDED_DIRS = {"__pycache__"}


def _current_git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def create_bundle(
    name: str,
    source_dir: Path = _DEFAULT_SOURCE,
    bundles_dir: Path = _DEFAULT_BUNDLES_DIR,
    force: bool = False,
) -> Path:
    """Copy source_dir to bundles_dir/name/, skipping excluded directories.

    Returns the path of the created bundle. Raises FileExistsError if the
    bundle already exists and force is False.
    """
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Source skills directory not found: {source_dir}")

    target = bundles_dir / name
    if target.exists():
        if not force:
            raise FileExistsError(
                f"Bundle already exists at {target}. Pass force=True to overwrite."
            )
        shutil.rmtree(target)

    bundles_dir.mkdir(parents=True, exist_ok=True)

    def _ignore(dir_path: str, contents: list[str]) -> list[str]:
        return [c for c in contents if c in _EXCLUDED_DIRS]

    shutil.copytree(source_dir, target, ignore=_ignore)

    # Write metadata
    meta = {
        "name": name,
        "created_at": datetime.utcnow().isoformat(),
        "source_dir": str(source_dir.resolve()),
        "git_sha": _current_git_sha(),
    }
    (target / ".bundle_meta.json").write_text(json.dumps(meta, indent=2))

    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Snapshot live skills as a named bundle")
    parser.add_argument("name", help="Bundle name (e.g., baseline_v1)")
    parser.add_argument("--force", action="store_true", help="Overwrite existing bundle")
    args = parser.parse_args()

    try:
        path = create_bundle(args.name, force=args.force)
    except FileExistsError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)

    print(f"Created bundle: {path}")


if __name__ == "__main__":
    main()
