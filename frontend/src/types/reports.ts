// Report response shapes — mirror the pydantic-style envelopes in
// `backend/api/reports.py`. Currency is hard-coded `'EUR'` until
// multi-currency lands. All `*_cents` are integer cents.

export type ReportBasis = 'cash' | 'accrual'

export type ReportType =
  | 'trial_balance'
  | 'balance_sheet'
  | 'income_statement'
  | 'cashflow'
  | 'budget_vs_actuals'
  | 'vat_return'

// GET /reports/trial_balance
export interface TrialBalanceLine {
  code: string
  name: string
  type: string
  total_debit_cents: number
  total_credit_cents: number
  balance_cents: number
}
export interface TrialBalanceResponse {
  as_of: string
  basis: ReportBasis
  currency: 'EUR'
  lines: TrialBalanceLine[]
  totals: {
    total_debit_cents: number
    total_credit_cents: number
    balanced: boolean
  }
}

// GET /reports/balance_sheet
export interface BalanceSheetLine {
  code: string
  name: string
  type: string
  balance_cents: number
}
export interface BalanceSheetResponse {
  as_of: string
  basis: ReportBasis
  currency: 'EUR'
  sections: {
    assets: BalanceSheetLine[]
    liabilities: BalanceSheetLine[]
    equity: BalanceSheetLine[]
  }
  totals: {
    total_assets_cents: number
    total_liabilities_equity_cents: number
    balanced: boolean
  }
  provisional: boolean
}

// GET /reports/income_statement
export interface IncomeStatementLine {
  code: string
  name: string
  balance_cents: number
}
export interface IncomeStatementResponse {
  from: string
  to: string
  basis: ReportBasis
  currency: 'EUR'
  sections: {
    revenue: IncomeStatementLine[]
    expense: IncomeStatementLine[]
  }
  totals: {
    total_revenue_cents: number
    total_expense_cents: number
    net_income_cents: number
  }
}

// GET /reports/cashflow
export interface CashflowResponse {
  from: string
  to: string
  currency: 'EUR'
  sections: {
    operating_cents: number
    investing_cents: number
    financing_cents: number
  }
  totals: {
    net_change_cents: number
    opening_balance_cents: number
    closing_balance_cents: number
  }
}

// GET /reports/budget_vs_actuals
export interface BudgetLine {
  envelope_id: number
  scope_kind: string
  scope_id: number | null
  category: string
  cap_cents: number
  used_cents: number
  remaining_cents: number
  pct_used: number
  allocation_count: number
}
export interface BudgetVsActualsResponse {
  period: string
  currency: 'EUR'
  lines: BudgetLine[]
  totals: {
    total_cap_cents: number
    total_used_cents: number
    total_remaining_cents: number
  }
}

// GET /reports/vat_return
export interface VatLine {
  gl_account: string
  rate_bp: number
  vat_cents: number
  debit_cents: number
  credit_cents: number
}
export interface VatReturnResponse {
  period: string
  currency: 'EUR'
  lines: VatLine[]
  totals: {
    collected_cents: number
    deductible_cents: number
    net_due_cents: number
  }
}

export type AnyReportResponse =
  | TrialBalanceResponse
  | BalanceSheetResponse
  | IncomeStatementResponse
  | CashflowResponse
  | BudgetVsActualsResponse
  | VatReturnResponse
