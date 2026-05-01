import { useLocation } from "react-router-dom";
import { ChevronRight, Sun, Moon, Eye, LogOut, ChevronDown, Search, Command } from "lucide-react";
import { useEffect, useState } from "react";
import { applyTheme, getTheme } from "@/lib/theme";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { MCPConnectButton } from "./MCPConnectDialog";

const ROUTE_NAMES: Record<string, string> = {
  "": "Today",
  me: "My spending",
  ledger: "Ledger",
  review: "Review",
  runs: "Runs",
  reports: "Reports",
  budgets: "Budgets",
  "ai-spend": "AI spend",
  wiki: "Wiki",
  onboarding: "Onboarding",
  employees: "Employees",
};

function currentPeriod(): string {
  const d = new Date();
  return d.toLocaleDateString(undefined, { month: "long", year: "numeric" });
}

export function FingentTopBar() {
  const location = useLocation();
  const segments = location.pathname.split("/").filter(Boolean);
  const crumb = segments.length === 0 ? "Today" : ROUTE_NAMES[segments[0]] ?? segments[0];

  const [theme, setTheme] = useState(getTheme());
  useEffect(() => applyTheme(theme), [theme]);

  return (
    <header className="flex h-16 shrink-0 items-center justify-between border-b border-border bg-background/80 px-6 backdrop-blur">
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 text-sec">
        <span className="text-muted-foreground">Fingent</span>
        <ChevronRight className="h-3.5 w-3.5 text-muted-foreground/50" />
        <span className="font-medium text-foreground">{crumb}</span>
      </div>

      {/* Right cluster */}
      <div className="flex items-center gap-2">
        {/* Search */}
        <button className="group hidden items-center gap-2 rounded-md border border-border bg-card/60 px-2.5 py-1.5 text-sec text-muted-foreground transition-colors hover:bg-card hover:text-foreground md:inline-flex">
          <Search className="h-3.5 w-3.5" />
          <span>Search</span>
          <span className="ml-6 inline-flex items-center gap-0.5 rounded border border-border bg-background px-1 font-mono text-[10px]">
            <Command className="h-2.5 w-2.5" />K
          </span>
        </button>

        {/* MCP connect */}
        <MCPConnectButton />

        {/* Period selector */}
        <button className="inline-flex items-center gap-2 rounded-md border border-border bg-card/60 px-3 py-1.5 text-sec transition-colors hover:bg-card">
          <span className="text-muted-foreground">Period</span>
          <span className="font-medium">{currentPeriod()}</span>
          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
        </button>

        {/* User menu */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button className="flex items-center gap-2 rounded-md border border-border bg-card/60 p-1 pr-2 transition-colors hover:bg-card">
              <div className="flex h-7 w-7 items-center justify-center rounded-sm bg-prism font-mono text-meta font-semibold text-primary-foreground">
                EL
              </div>
              <span className="hidden text-sec font-medium md:inline">Élise Laurent</span>
              <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-64 border-border bg-popover">
            <DropdownMenuLabel className="text-meta uppercase tracking-wide text-muted-foreground">
              Tenant
            </DropdownMenuLabel>
            <div className="px-2 pb-2 text-sec">Fingent Finance OÜ</div>
            <DropdownMenuSeparator className="bg-border" />
            <DropdownMenuLabel className="text-meta uppercase tracking-wide text-muted-foreground">
              Appearance
            </DropdownMenuLabel>
            {(["dark", "light"] as const).map((t) => {
              const Icon = t === "dark" ? Moon : Sun;
              return (
                <DropdownMenuItem key={t} onClick={() => setTheme(t)} className="gap-2">
                  <Icon className="h-3.5 w-3.5" />
                  <span className="capitalize">{t}</span>
                  <span className={cn("ml-auto text-meta text-primary", theme === t ? "opacity-100" : "opacity-0")}>
                    ✓
                  </span>
                </DropdownMenuItem>
              );
            })}
            <DropdownMenuSeparator className="bg-border" />
            <DropdownMenuItem className="gap-2">
              <Eye className="h-3.5 w-3.5" />
              View as auditor
              <span className="ml-auto rounded-sm bg-muted px-1.5 py-0.5 text-meta text-muted-foreground">read-only</span>
            </DropdownMenuItem>
            <DropdownMenuSeparator className="bg-border" />
            <DropdownMenuItem className="gap-2 text-muted-foreground">
              <LogOut className="h-3.5 w-3.5" />
              Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
