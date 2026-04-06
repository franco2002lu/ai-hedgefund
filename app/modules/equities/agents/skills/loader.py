"""Skill loader — composes system prompts from layered markdown skill files.

Skills are additive: base + branch overlay + sector overlay + output format.
Missing overlays are silently skipped (graceful degradation).
Results are cached via lru_cache so the same (analyst_type, branch, sector)
tuple always returns the same string object — important for Anthropic prompt
caching, which hashes system content.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).parent

_SEPARATOR = "\n\n---\n\n"


class MissingSkillError(Exception):
    """Raised when a required skill file is missing from the skills directory."""


def _read_skill(path: Path) -> str | None:
    """Read a skill file, returning None if it doesn't exist."""
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return None


@lru_cache(maxsize=1)
def _load_output_format() -> str:
    """Load the shared output format layer.

    This layer is required — every composed prompt must end with it so analysts
    know the JSON schema and pre-response checklist. Cached because the file
    contents never change at runtime, and identical strings are needed for
    Anthropic prompt caching.
    """
    path = _SKILLS_DIR / "output_format.md"
    content = _read_skill(path)
    if content is None:
        raise MissingSkillError(
            f"Required output format skill not found at {path}"
        )
    return content


def _normalize_branch(branch_name: str) -> str:
    """Strip test_ prefix and extract the core branch name (growth/value)."""
    name = branch_name.lower().strip()
    # Strip test_ prefix: test_growth -> growth, test_value -> value
    if name.startswith("test_"):
        name = name[5:]
    return name


def _normalize_sector(sector: str) -> str:
    """Convert sector name to a filesystem-safe key."""
    return sector.lower().strip().replace(" ", "_").replace("&", "and")


@lru_cache(maxsize=64)
def compose_system_prompt(
    analyst_type: str,
    branch_name: str = "",
    sector: str | None = None,
) -> str:
    """Compose system prompt by layering: base + branch + sector + output format.

    Args:
        analyst_type: "fundamentals" | "technical" | "news"
        branch_name: "growth" | "value" | "test_growth" | "" etc.
        sector: GICS sector from UniverseStock (optional, for future sector overlays)

    Returns:
        Composed system prompt string with all applicable layers joined by --- separators.
    """
    layers: list[str] = []

    # 1. Base skill (required)
    base = _read_skill(_SKILLS_DIR / "base" / f"{analyst_type}.md")
    if base:
        layers.append(base)
    else:
        logger.warning("Missing base skill for analyst_type=%s", analyst_type)

    # 2. Branch overlay (optional)
    if branch_name:
        branch_key = _normalize_branch(branch_name)
        branch_skill = _read_skill(
            _SKILLS_DIR / "branches" / branch_key / f"{analyst_type}.md"
        )
        if branch_skill:
            layers.append(branch_skill)

    # 3. Sector overlay (optional, Phase 2)
    if sector:
        sector_key = _normalize_sector(sector)
        sector_skill = _read_skill(_SKILLS_DIR / "sectors" / f"{sector_key}.md")
        if sector_skill:
            layers.append(sector_skill)

    # 4. Output format (always last, required)
    layers.append(_load_output_format())

    return _SEPARATOR.join(layers)


def get_available_skills() -> dict:
    """Return a summary of discovered skill files for diagnostics."""
    result: dict = {"base": {}, "branches": {}, "sectors": {}}

    # Base skills
    base_dir = _SKILLS_DIR / "base"
    if base_dir.is_dir():
        for f in sorted(base_dir.glob("*.md")):
            result["base"][f.stem] = str(f)

    # Branch overlays
    branches_dir = _SKILLS_DIR / "branches"
    if branches_dir.is_dir():
        for branch_dir in sorted(branches_dir.iterdir()):
            if branch_dir.is_dir():
                result["branches"][branch_dir.name] = {}
                for f in sorted(branch_dir.glob("*.md")):
                    result["branches"][branch_dir.name][f.stem] = str(f)

    # Sector overlays
    sectors_dir = _SKILLS_DIR / "sectors"
    if sectors_dir.is_dir():
        for f in sorted(sectors_dir.glob("*.md")):
            result["sectors"][f.stem] = str(f)

    return result
