---
applies_to: [dinners, fr, bewirtung]
jurisdictions: [FR]
threshold_eur: 250
revision: 1
agent_input_for: [gl_account_classifier_agent, document_extractor]
---

# FR — Business meals (repas d'affaires)

Apply when the line item is a restaurant invoice or a meal expense in France.

## GL account

- PCG **625710 — Réceptions** for business meals with clients or prospects.
- PCG **625100 — Voyages et déplacements** is wrong here; that code is for
  travel costs (taxis, train, airfare), not meals.

## VAT

- TVA at 10% on restaurant meals (rate 1000 bp, account 4456).
- TVA on alcohol within a restaurant invoice is **not** deductible — the
  extractor must split if the receipt itemises alcohol.

## Documentation requirements

- Above €250 (the `threshold_eur` in this page's frontmatter): attendees list
  is mandatory. Names + companies + business purpose.
- Below €250: business purpose only.

If the threshold is crossed and the attendees list is missing, the line is
flagged `review_required` and routed to the human queue. Do not auto-post.

## Anti-patterns

- Do not classify a personal meal (employee dining alone) as `625710`. That's
  a payroll-side benefit (BIK) and posts via the benefit pipelines, not the
  ordinary expense path.
