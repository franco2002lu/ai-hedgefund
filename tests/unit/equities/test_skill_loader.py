"""Unit tests for the skill loader — compose_system_prompt() and get_available_skills()."""

import pytest

from app.modules.equities.agents.skills.loader import (
    compose_system_prompt,
    get_available_skills,
)

# ---------------------------------------------------------------------------
# compose_system_prompt tests
# ---------------------------------------------------------------------------


class TestComposeSystemPrompt:
    def test_base_only_when_no_branch_or_sector(self):
        """With no branch or sector, result contains base skill + output format."""
        prompt = compose_system_prompt("fundamentals")

        assert "Fundamentals Analyst" in prompt
        assert "Analysis Framework" in prompt
        assert "Score Calibration" in prompt
        # Output format is always appended
        assert "bullish_score" in prompt
        assert "JSON" in prompt

    def test_branch_overlay_included_for_growth(self):
        """Growth branch overlay is appended after the base."""
        prompt = compose_system_prompt("fundamentals", "growth")

        # Base content present
        assert "Fundamentals Analyst" in prompt
        # Branch overlay present
        assert "Growth Branch" in prompt
        assert "Revenue Growth" in prompt
        assert "PEG" in prompt

    def test_branch_overlay_included_for_value(self):
        """Value branch overlay is appended after the base."""
        prompt = compose_system_prompt("fundamentals", "value")

        assert "Fundamentals Analyst" in prompt
        assert "Value Branch" in prompt
        assert "FCF Yield" in prompt

    def test_all_analyst_types_have_base_skills(self):
        """All 3 analyst types produce a non-trivial prompt from base alone."""
        for analyst_type in ("fundamentals", "technical", "news"):
            prompt = compose_system_prompt(analyst_type)
            assert len(prompt) > 200, f"Base skill for {analyst_type} is too short"
            assert "Score Calibration" in prompt

    def test_test_prefix_stripped(self):
        """branch_name 'test_growth' is treated as 'growth'."""
        prompt_test = compose_system_prompt("fundamentals", "test_growth")
        prompt_direct = compose_system_prompt("fundamentals", "growth")

        # Both should contain the growth overlay
        assert "Growth Branch" in prompt_test
        # Content should be identical
        assert prompt_test == prompt_direct

    def test_test_prefix_stripped_value(self):
        """branch_name 'test_value' is treated as 'value'."""
        prompt_test = compose_system_prompt("technical", "test_value")
        prompt_direct = compose_system_prompt("technical", "value")

        assert "Value Branch" in prompt_test
        assert prompt_test == prompt_direct

    def test_missing_branch_graceful(self):
        """Unknown branch name is silently skipped — only base + output format."""
        prompt = compose_system_prompt("fundamentals", "unknown_strategy")

        assert "Fundamentals Analyst" in prompt
        # Should not crash, just no overlay
        assert "Branch" not in prompt or "Growth Branch" not in prompt

    def test_missing_sector_graceful(self):
        """Unknown sector is silently skipped."""
        prompt = compose_system_prompt("fundamentals", "growth", "Nonexistent Sector")

        # Should still have base + branch, just no sector
        assert "Fundamentals Analyst" in prompt
        assert "Growth Branch" in prompt

    def test_separator_between_layers(self):
        """Layers are joined with --- separators."""
        prompt = compose_system_prompt("fundamentals", "growth")

        assert "---" in prompt

    def test_output_format_always_last(self):
        """Output format instruction is at the end of the composed prompt."""
        prompt = compose_system_prompt("fundamentals", "growth")

        # The output format should come after the branch overlay
        last_section_idx = prompt.rfind("---")
        output_section = prompt[last_section_idx:]
        assert "JSON" in output_section
        assert "bullish_score" in output_section

    def test_caching_returns_same_object(self):
        """lru_cache means same args return the same string object (identity check)."""
        prompt1 = compose_system_prompt("technical", "growth")
        prompt2 = compose_system_prompt("technical", "growth")

        assert prompt1 is prompt2  # Same object, not just equal

    def test_different_args_return_different_prompts(self):
        """Different analyst types produce different prompts."""
        p_fund = compose_system_prompt("fundamentals", "growth")
        p_tech = compose_system_prompt("technical", "growth")
        p_news = compose_system_prompt("news", "growth")

        assert p_fund != p_tech
        assert p_tech != p_news

    def test_branch_adds_content(self):
        """Branch overlay makes the prompt longer than base-only."""
        base_only = compose_system_prompt("news")
        with_branch = compose_system_prompt("news", "growth")

        assert len(with_branch) > len(base_only)


# ---------------------------------------------------------------------------
# get_available_skills tests
# ---------------------------------------------------------------------------


class TestGetAvailableSkills:
    def test_returns_dict(self):
        skills = get_available_skills()
        assert isinstance(skills, dict)

    def test_has_base_skills(self):
        skills = get_available_skills()
        assert "base" in skills
        base = skills["base"]
        assert "fundamentals" in base
        assert "technical" in base
        assert "news" in base

    def test_has_branch_skills(self):
        skills = get_available_skills()
        assert "branches" in skills
        branches = skills["branches"]
        assert "growth" in branches
        assert "value" in branches

    def test_growth_branch_has_all_analysts(self):
        skills = get_available_skills()
        growth = skills["branches"]["growth"]
        assert "fundamentals" in growth
        assert "technical" in growth
        assert "news" in growth

    def test_value_branch_has_all_analysts(self):
        skills = get_available_skills()
        value = skills["branches"]["value"]
        assert "fundamentals" in value
        assert "technical" in value
        assert "news" in value
