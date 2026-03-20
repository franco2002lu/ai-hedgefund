"""LLM client wrapper for analyst agents."""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)


class AnthropicAnalystClient:
    """Thin wrapper around the Anthropic SDK that returns a dict.

    Mirrors the interface expected by analyst agents:
        result = await llm_client.invoke(prompt)
    where result is a dict with keys: bullish_score, confidence, summary.
    """

    def __init__(self, model: str = "claude-sonnet-4-6", temperature: float = 0.3):
        self._client = None
        self.model = model
        self.temperature = temperature

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            logger.warning("ANTHROPIC_API_KEY not set — LLM analysts will be unavailable")
            return

        import anthropic

        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    _DEFAULT_SYSTEM_MSG = (
        "You are a financial analyst assistant. Respond ONLY with a JSON object "
        "containing exactly three keys:\n"
        '  "bullish_score": integer 1-10 (1=very bearish, 10=very bullish)\n'
        '  "confidence": integer 1-10 (1=very uncertain, 10=very certain)\n'
        '  "summary": string (1-2 sentence analysis summary)\n'
        "Do not include any text outside the JSON object."
    )

    async def invoke(self, prompt: str, *, system_prompt: str | None = None) -> dict:
        """Send prompt to Claude and parse a structured analyst response.

        Args:
            prompt: User prompt (context + analysis instruction).
            system_prompt: Custom system prompt from skill composition.
                When provided, sent as list format with cache_control for
                Anthropic prompt caching. When None, falls back to the
                default legacy system message.
        """
        if self._client is None:
            raise RuntimeError("ANTHROPIC_API_KEY not set — cannot invoke LLM analyst")

        if system_prompt is not None:
            system = [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            system = self._DEFAULT_SYSTEM_MSG

        response = await self._client.messages.create(
            model=self.model,
            max_tokens=512,
            temperature=self.temperature,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text.strip()

        # Strip markdown code fences if present
        backtick3 = chr(96) * 3
        if text.startswith(backtick3):
            text_lines = text.split("\n")
            text_lines = [ln for ln in text_lines if not ln.strip().startswith(backtick3)]
            text = "\n".join(text_lines).strip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = {
                "bullish_score": 5,
                "confidence": 5,
                "summary": text[:200] if text else "Unable to parse LLM response.",
            }

        # Clamp scores to valid range
        bs = parsed.get("bullish_score", 5)
        conf = parsed.get("confidence", 5)
        parsed["bullish_score"] = max(1, min(10, int(bs)))
        parsed["confidence"] = max(1, min(10, int(conf)))
        if "summary" not in parsed or not parsed["summary"]:
            parsed["summary"] = "No analysis available."

        return parsed
