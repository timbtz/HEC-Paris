export const centsToEuros = (
  cents: number,
  opts: { signed?: boolean } = {},
): string => {
  const sign = cents < 0 ? '-' : opts.signed && cents > 0 ? '+' : ''
  const abs = Math.abs(cents) / 100
  return `${sign}€${abs.toFixed(2)}`
}

export const shortDate = (iso: string): string => {
  // '2026-04-15' → '15 Apr'
  try {
    const d = new Date(iso)
    return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' })
  } catch {
    return iso
  }
}

export const categoryLabel: Record<string, string> = {
  food: 'Food',
  travel: 'Travel',
  saas: 'SaaS',
  ai_tokens: 'AI',
  leasing: 'Leasing',
}
