"""Unit tests for the skill loader — compose_system_prompt() and get_available_skills()."""

import pytest

from app.modules.equities.agents.skills import loader
from app.modules.equities.agents.skills.loader import (
    MissingSkillError,
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


# ---------------------------------------------------------------------------
# Output format layer tests
# ---------------------------------------------------------------------------


class TestOutputFormatLayer:
    """Tests for the shared output_format.md layer extracted from loader.py."""

    def test_load_output_format_returns_non_empty_content(self):
        content = loader._load_output_format("")
        assert content
        assert "bullish_score" in content
        assert "confidence" in content
        assert "summary" in content

    def test_output_format_contains_pre_response_checklist(self):
        content = loader._load_output_format("")
        assert "Before You Respond" in content

    def test_missing_output_format_raises_missing_skill_error(self, monkeypatch, tmp_path):
        """If output_format.md is missing, _load_output_format raises MissingSkillError."""
        loader._load_output_format.cache_clear()
        monkeypatch.setattr(loader, "_SKILLS_DIR", tmp_path)
        with pytest.raises(MissingSkillError, match="output format"):
            loader._load_output_format("")
        # Restore the real cache for downstream tests
        loader._load_output_format.cache_clear()


# ---------------------------------------------------------------------------
# Critical Reminders section tests
# ---------------------------------------------------------------------------


class TestCriticalReminders:
    """All base analyst skills must surface critical reminders near the top."""

    @pytest.mark.parametrize("analyst_type", ["fundamentals", "news", "technical"])
    def test_critical_reminders_section_present(self, analyst_type):
        prompt = compose_system_prompt(analyst_type)
        assert "## Critical Reminders" in prompt

    @pytest.mark.parametrize("analyst_type", ["fundamentals", "news", "technical"])
    def test_critical_reminders_appear_before_analysis_framework(self, analyst_type):
        """The reminders block must come before the framework so the model reads it first."""
        prompt = compose_system_prompt(analyst_type)
        reminders_idx = prompt.find("## Critical Reminders")
        framework_idx = prompt.find("## Analysis Framework")
        assert reminders_idx != -1
        assert framework_idx != -1
        assert reminders_idx < framework_idx


# ---------------------------------------------------------------------------
# skills_dir parameter tests
# ---------------------------------------------------------------------------


class TestSkillsDirParameter:
    """compose_system_prompt must accept an optional skills_dir override so
    backtests can load alternate skill bundles without mutating the module-level
    _SKILLS_DIR constant."""

    def test_default_none_uses_package_skills(self):
        """Passing skills_dir=None behaves identically to omitting it."""
        loader._load_output_format.cache_clear()
        compose_system_prompt.cache_clear()
        prompt_default = compose_system_prompt("fundamentals", "growth")
        prompt_explicit_none = compose_system_prompt("fundamentals", "growth", None, None)
        assert prompt_default == prompt_explicit_none

    def test_alternate_skills_dir_loads_different_content(self, tmp_path):
        """Pointing skills_dir at a directory with different files produces
        a different composed prompt."""
        loader._load_output_format.cache_clear()
        compose_system_prompt.cache_clear()

        # Build a minimal alternate bundle
        (tmp_path / "base").mkdir()
        (tmp_path / "base" / "fundamentals.md").write_text("# Alternate Fundamentals Skill\n\nAlternate instructions.")
        (tmp_path / "output_format.md").write_text('## Output Format\n\nReturn JSON with "bullish_score".')

        prompt = compose_system_prompt("fundamentals", "", None, tmp_path)
        assert "Alternate Fundamentals Skill" in prompt
        assert "Alternate instructions" in prompt
        # Output format layer still appended
        assert "bullish_score" in prompt

    def test_different_skills_dirs_cached_separately(self, tmp_path):
        """The lru_cache key must include skills_dir so two bundles don't collide."""
        loader._load_output_format.cache_clear()
        compose_system_prompt.cache_clear()

        # Build two minimal alternate bundles
        dir_a = tmp_path / "bundle_a"
        dir_b = tmp_path / "bundle_b"
        for d, label in ((dir_a, "A"), (dir_b, "B")):
            (d / "base").mkdir(parents=True)
            (d / "base" / "fundamentals.md").write_text(f"# Bundle {label}")
            (d / "output_format.md").write_text("## Output Format\n\nReturn JSON with bullish_score.")

        prompt_a = compose_system_prompt("fundamentals", "", None, dir_a)
        prompt_b = compose_system_prompt("fundamentals", "", None, dir_b)
        assert "Bundle A" in prompt_a
        assert "Bundle B" in prompt_b
        assert prompt_a != prompt_b


# ---------------------------------------------------------------------------
# News-prompt-specific structural tests (post-2026-04-16 redesign)
# ---------------------------------------------------------------------------


class TestNewsPromptStructure:
    """Structural tests for the rewritten news analyst base prompt."""

    def test_news_base_contains_new_section_markers(self):
        """The rewritten news base prompt has the 8-section structure."""
        prompt = compose_system_prompt("news")
        assert "# News Analyst" in prompt
        assert "## Role" in prompt
        assert "## Critical Reminders" in prompt
        assert "## Input Shape" in prompt
        assert "## Analysis Framework" in prompt
        assert "## Stock-Exposure Assessment" in prompt
        assert "## Score Calibration" in prompt
        assert "## Confidence Calibration" in prompt
        assert "## Worked Examples" in prompt
        assert "## Common Failure Modes" in prompt

    def test_news_base_section_order(self):
        """Sections appear in the documented order."""
        prompt = compose_system_prompt("news")
        order = [
            "# News Analyst",
            "## Role",
            "## Critical Reminders",
            "## Input Shape",
            "## Analysis Framework",
            "## Stock-Exposure Assessment",
            "## Score Calibration",
            "## Confidence Calibration",
            "## Worked Examples",
            "## Common Failure Modes",
        ]
        positions = [prompt.find(marker) for marker in order]
        assert all(p >= 0 for p in positions), (
            f"Missing markers: {[m for m, p in zip(order, positions, strict=False) if p < 0]}"
        )
        assert positions == sorted(positions), f"Out of order: {positions}"

    def test_news_base_references_three_layers(self):
        """The base prompt explicitly names the three input layers."""
        prompt = compose_system_prompt("news").lower()
        assert "market" in prompt
        assert "sector" in prompt
        assert "stock-specific" in prompt


class TestNewsPromptStalePhraseRegression:
    """Prevent known-stale phrases from reappearing in the news base prompt."""

    _STALE_PHRASES = [
        "absence of news is mildly positive",
        "press releases",
        "SEC investigation",
        "Tim Cook",
        "antitrust probe",
    ]

    @pytest.mark.parametrize("phrase", _STALE_PHRASES)
    def test_base_news_does_not_contain_stale_phrase(self, phrase):
        prompt = compose_system_prompt("news")
        assert phrase.lower() not in prompt.lower(), f"Stale phrase '{phrase}' still present in news base prompt"


# ---------------------------------------------------------------------------
# News overlay structural tests (post-2026-04-16 redesign)
# ---------------------------------------------------------------------------


class TestNewsGrowthOverlayStructure:
    """The rewritten growth overlay uses thesis + layer-guidance structure."""

    def test_growth_overlay_has_investment_thesis_section(self):
        prompt = compose_system_prompt("news", "growth")
        assert "# Growth Branch" in prompt
        assert "## Investment Thesis" in prompt

    def test_growth_overlay_has_layer_guidance_section(self):
        prompt = compose_system_prompt("news", "growth")
        assert "## Reading the Three Layers" in prompt

    def test_growth_overlay_has_stock_exposure_emphasis(self):
        prompt = compose_system_prompt("news", "growth")
        assert "## Stock-Exposure Emphasis" in prompt

    def test_growth_overlay_has_signal_strength_section(self):
        prompt = compose_system_prompt("news", "growth")
        assert "## Signals That Warrant Strong Conviction" in prompt


class TestNewsGrowthOverlayNoMechanicalModifiers:
    """The overlay must not prescribe numeric score offsets.

    The old overlay had lines like '+1', '-2' in bold. The new design
    trusts the LLM to judge magnitude based on the base prompt's Score
    Calibration. This regression test ensures the old mechanical pattern
    does not reappear.
    """

    def test_growth_overlay_no_bold_numeric_modifiers(self):
        """No **+N** or **-N** pattern anywhere in the growth overlay."""
        import re

        prompt = compose_system_prompt("news", "growth")
        # Narrow regex: match **+1**, **+ 2**, **-2**, etc. — the exact
        # formatting the old overlay used. Avoids false positives on
        # legitimate content like "rising rates by 50bps" or "10-year yield".
        pattern = re.compile(r"\*\*\s*[+-]\s*\d+\s*\*\*")
        matches = pattern.findall(prompt)
        assert not matches, f"Old mechanical-modifier pattern found: {matches}"


class TestNewsValueOverlayStructure:
    """The rewritten value overlay uses thesis + layer-guidance structure."""

    def test_value_overlay_has_investment_thesis_section(self):
        prompt = compose_system_prompt("news", "value")
        assert "# Value Branch" in prompt
        assert "## Investment Thesis" in prompt

    def test_value_overlay_has_layer_guidance_section(self):
        prompt = compose_system_prompt("news", "value")
        assert "## Reading the Three Layers" in prompt

    def test_value_overlay_has_stock_exposure_emphasis(self):
        prompt = compose_system_prompt("news", "value")
        assert "## Stock-Exposure Emphasis" in prompt

    def test_value_overlay_has_signal_strength_section(self):
        prompt = compose_system_prompt("news", "value")
        assert "## Signals That Warrant Strong Conviction" in prompt


class TestNewsValueOverlayNoMechanicalModifiers:
    def test_value_overlay_no_bold_numeric_modifiers(self):
        import re

        prompt = compose_system_prompt("news", "value")
        pattern = re.compile(r"\*\*\s*[+-]\s*\d+\s*\*\*")
        matches = pattern.findall(prompt)
        assert not matches, f"Old mechanical-modifier pattern found: {matches}"


# ---------------------------------------------------------------------------
# Item 3 + 6 follow-up tests (post-2026-04-16 refinements)
# ---------------------------------------------------------------------------


class TestBaseNewsPromptGrounding:
    """Item 3: base prompt has explicit grounding guidance for prior-knowledge use."""

    def test_base_has_grounding_subsection(self):
        prompt = compose_system_prompt("news")
        assert "Grounding when using prior knowledge" in prompt

    def test_grounding_requires_summary_disclosure(self):
        """The grounding section must instruct the LLM to name its prior knowledge in the summary."""
        prompt = compose_system_prompt("news")
        idx = prompt.find("Grounding when using prior knowledge")
        assert idx >= 0
        section = prompt[idx : idx + 1500]
        assert "summary" in section.lower()
        assert "confidence" in section.lower()

    def test_critical_reminders_mentions_prior_knowledge_grounding(self):
        """A Critical Reminders bullet must call out prior-knowledge grounding."""
        prompt = compose_system_prompt("news")
        reminders_start = prompt.find("## Critical Reminders")
        reminders_end = prompt.find("## Input Shape")
        reminders = prompt[reminders_start:reminders_end]
        assert "prior knowledge" in reminders.lower()


class TestBaseNewsPromptFailureModeWarnings:
    """Item 6: base prompt calls out three-layer-specific failure modes."""

    def test_critical_reminders_warns_against_uniform_macro_application(self):
        prompt = compose_system_prompt("news")
        reminders_start = prompt.find("## Critical Reminders")
        reminders_end = prompt.find("## Input Shape")
        reminders = prompt[reminders_start:reminders_end]
        assert "uniform" in reminders.lower() or "uniformly" in reminders.lower()

    def test_critical_reminders_warns_about_sub_sector_themes(self):
        prompt = compose_system_prompt("news")
        reminders_start = prompt.find("## Critical Reminders")
        reminders_end = prompt.find("## Input Shape")
        reminders = prompt[reminders_start:reminders_end]
        assert "sub-sector" in reminders.lower()

    def test_failure_modes_warns_about_regime_classifier_collapse(self):
        prompt = compose_system_prompt("news")
        fm_start = prompt.find("## Common Failure Modes")
        fm = prompt[fm_start:]
        assert "regime classifier" in fm.lower()

    def test_failure_modes_warns_about_article_classification(self):
        prompt = compose_system_prompt("news")
        fm_start = prompt.find("## Common Failure Modes")
        fm = prompt[fm_start:]
        assert "classify" in fm.lower()
        assert "scope" in fm.lower()


# ---------------------------------------------------------------------------
# Item 4: growth overlay zone anchors tests
# ---------------------------------------------------------------------------


class TestNewsGrowthOverlayZoneAnchors:
    """Item 4: growth overlay's Signals section has zone anchors tied to the calibration scale."""

    def test_growth_overlay_has_moderately_bullish_zone(self):
        prompt = compose_system_prompt("news", "growth")
        assert "Moderately bullish" in prompt

    def test_growth_overlay_has_moderately_bearish_zone(self):
        prompt = compose_system_prompt("news", "growth")
        assert "Moderately bearish" in prompt

    def test_growth_overlay_anchors_strong_signals_to_calibration_zones(self):
        """Strong signals must reference specific calibration zones (8-9, 2-3)."""
        prompt = compose_system_prompt("news", "growth")
        idx = prompt.find("## Signals That Warrant Strong Conviction")
        assert idx >= 0
        section = prompt[idx:]
        assert "8-9" in section, "Missing strong-bullish zone anchor (8-9)"
        assert "2-3" in section, "Missing strong-bearish zone anchor (2-3)"

    def test_growth_overlay_anchors_moderate_signals_to_calibration_zones(self):
        """Moderate signals must reference calibration zones (6-7 bullish)."""
        prompt = compose_system_prompt("news", "growth")
        idx = prompt.find("## Signals That Warrant Strong Conviction")
        section = prompt[idx:]
        assert "6-7" in section, "Missing moderate-bullish zone anchor (6-7)"

    def test_growth_overlay_still_has_no_bold_numeric_modifiers(self):
        """Zone anchors must not reintroduce the old **+N**/**-N** pattern."""
        import re

        prompt = compose_system_prompt("news", "growth")
        pattern = re.compile(r"\*\*\s*[+-]\s*\d+\s*\*\*")
        matches = pattern.findall(prompt)
        assert not matches, f"Mechanical-modifier pattern reappeared: {matches}"


class TestNewsValueOverlayZoneAnchors:
    """Item 4: value overlay's Signals section has zone anchors tied to the calibration scale."""

    def test_value_overlay_has_moderately_bullish_zone(self):
        prompt = compose_system_prompt("news", "value")
        assert "Moderately bullish" in prompt

    def test_value_overlay_has_moderately_bearish_zone(self):
        prompt = compose_system_prompt("news", "value")
        assert "Moderately bearish" in prompt

    def test_value_overlay_anchors_strong_signals_to_calibration_zones(self):
        prompt = compose_system_prompt("news", "value")
        idx = prompt.find("## Signals That Warrant Strong Conviction")
        assert idx >= 0
        section = prompt[idx:]
        assert "8-9" in section, "Missing strong-bullish zone anchor (8-9)"
        assert "2-3" in section, "Missing strong-bearish zone anchor (2-3)"

    def test_value_overlay_anchors_moderate_signals_to_calibration_zones(self):
        prompt = compose_system_prompt("news", "value")
        idx = prompt.find("## Signals That Warrant Strong Conviction")
        section = prompt[idx:]
        assert "6-7" in section, "Missing moderate-bullish zone anchor (6-7)"

    def test_value_overlay_still_has_no_bold_numeric_modifiers(self):
        import re

        prompt = compose_system_prompt("news", "value")
        pattern = re.compile(r"\*\*\s*[+-]\s*\d+\s*\*\*")
        matches = pattern.findall(prompt)
        assert not matches, f"Mechanical-modifier pattern reappeared: {matches}"


# ---------------------------------------------------------------------------
# Item 5: Parallel structure invariant test
# ---------------------------------------------------------------------------


class TestOverlaysHaveParallelStructure:
    """Item 5: growth and value overlays must have the same H2 section headers.

    Prevents drift when someone edits one overlay but forgets to mirror
    the change in the other.
    """

    def test_growth_and_value_overlay_sections_match(self):
        import re

        g_prompt = compose_system_prompt("news", "growth")
        v_prompt = compose_system_prompt("news", "value")

        # Extract H2 headers from the overlay portion. The overlay starts at
        # the branch header (# Growth Branch / # Value Branch) and ends at
        # the next --- separator that joins the output format layer.
        g_overlay = g_prompt.split("# Growth Branch")[1].split("\n---\n")[0]
        v_overlay = v_prompt.split("# Value Branch")[1].split("\n---\n")[0]

        g_sections = re.findall(r"^## .+$", g_overlay, re.MULTILINE)
        v_sections = re.findall(r"^## .+$", v_overlay, re.MULTILINE)

        assert g_sections == v_sections, (
            f"Overlay structure drift.\n  Growth sections: {g_sections}\n  Value sections:  {v_sections}"
        )


# ---------------------------------------------------------------------------
# compose_ranking_prompt tests
# ---------------------------------------------------------------------------


class TestComposeRankingPrompt:
    def test_compose_ranking_prompt_loads_base_ranking_skill(self):
        from app.modules.equities.agents.skills.loader import compose_ranking_prompt

        prompt = compose_ranking_prompt("news", "growth")
        assert "rank" in prompt.lower()
        # analyst_type placeholder substituted
        assert "{analyst_type}" not in prompt
        assert "news" in prompt
        # must NOT append the per-stock output format (different response schema)
        assert "bullish_score" not in prompt
