---
applies_to: [dinners, de, bewirtung, bik]
jurisdictions: [DE]
threshold_eur: 250
revision: 1
agent_input_for: [gl_account_classifier_agent, document_extractor]
---

# DE — Business meals (Bewirtungskosten)

Apply when the line item is a restaurant invoice or a meal expense in Germany.

## GL account

- SKR04 **6640 — Bewirtungskosten** when the meal has a verifiable business
  purpose (clients, prospects, partners) — 70% deductible income-tax-wise
  but the expense is booked at 100% on this code; the 30% non-deductible
  portion lands on **6645** at year-end via the close pipeline.
- Pure staff meals (no external attendee) belong on **6130 — Personalaufwand**
  and are subject to the Sachbezug €50/month BIK ceiling.

## VAT

- 19% on alcohol; 7% on food and non-alcoholic drinks from 2026-01-01
  (BMF restaurant-VAT cut). The extractor must split.
- Input VAT 100% recoverable IF the Bewirtungsbeleg names guests + business
  reason; otherwise it stays unrecovered.
- Above €250 the receipt must additionally show the company's full address.

## Documentation requirements

- Above €250 (the `threshold_eur`): attendees list, business reason,
  company-address-on-invoice. Missing any → flag `review_required`.
- Below €250: business reason only is enough.

## Anti-patterns

- Do not split a single Bewirtungsbeleg into multiple JEs to dodge the €250
  threshold. The extractor enforces aggregation per receipt id.
