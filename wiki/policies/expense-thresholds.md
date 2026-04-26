---
applies_to: [thresholds, expense, gating, classification]
revision: 1
agent_input_for: [gl_account_classifier_agent, counterparty_classifier, anomaly_flag_agent]
---

# Expense policy thresholds

Numeric guards the gating, classification, and review-routing tools all read.
Every threshold is in EUR (no cents). Confidence is a probability in [0, 1].

## Auto-post confidence floor

- `confidence_gate`: auto-post only when the GL classifier's confidence is
  ≥ **0.85**. Below that, the entry lands on the review queue with the
  classifier's top alternative captured for the human to compare.

## Receipt-required threshold

- Below **€25**: receipt optional (in line with the URSSAF / DE de-minimis
  conventions; no statutory storage requirement at this size).
- ≥ **€25**: receipt required. Missing receipt → flag `review_required`,
  do not post.

## Manager / CFO approval thresholds

- ≥ **€500**: manager pre-approval required before posting (Slack /
  Telegram approval gateway). The pipeline still posts automatically once
  the approver hits OK.
- ≥ **€2,500**: CFO co-approval required in addition to the manager.
  Two approvers must clear the entry before `gl_poster.post` runs.

## Anomaly flags

- Repeated counterparty + amount combinations within a 7-day window are
  flagged for the `anomaly_flag_agent`. Pattern: same vendor, same cents,
  ≥ 3 occurrences ⇒ duplicate-charge candidate.

## Why these numbers

- 0.85 confidence is calibrated against the Sonnet 4.6 `gl_account_classifier`
  micro-eval: above 0.85 the realised error rate drops below 2%.
- €25 / €500 / €2,500 line up with FR PCG / DE GoBD documentation
  expectations and are the defaults in the onboarding wizard. A tenant
  can override per the frontmatter on a tenant-specific page.
