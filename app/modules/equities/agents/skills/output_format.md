## Before You Respond

1. Count your bullish vs bearish signals from the analysis above.
2. Check your score against the Score Calibration table — does the label match your reasoning?
3. Check your confidence against the Confidence Calibration table.
4. Ensure your summary cites at least 2 specific data points (e.g., exact metric values, headline text).

## Output Format

Respond ONLY with a JSON object containing exactly three keys:
  "bullish_score": integer 1-10 (1=very bearish, 10=very bullish)
  "confidence": integer 1-10 — the likelihood your directional call resolves
    correctly within ~1 month. 1 = coin flip, 5 = modest edge, 10 = near
    certainty backed by concrete, dated evidence. Score your evidence, not your
    conviction: vague positives are low confidence.
  "summary": string (2-4 sentences citing specific data points from the analysis)

Do not include any text outside the JSON object.
