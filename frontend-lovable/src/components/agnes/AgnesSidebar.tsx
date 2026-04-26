import { NavLink, useLocation } from "react-router-dom";
import {
  LayoutDashboard,
  BookText,
  ListChecks,
  Workflow,
  FileBarChart2,
  Wallet,
  Cpu,
  BookOpen,
  Sparkles,
  Trophy,
  Wallet2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { LiveDot } from "./primitives";
import { useDashboard } from "@/store/dashboard";

interface NavItem {
  to: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}

const NAV_PRIMARY: NavItem[] = [
  { to: "/", label: "Today", icon: LayoutDashboard },
  { to: "/me", label: "My spending", icon: Wallet2 },
];

const NAV_FINANCE: NavItem[] = [
  { to: "/ledger", label: "Ledger", icon: BookText },
  { to: "/review", label: "Review", icon: ListChecks },
  { to: "/runs", label: "Runs", icon: Workflow },
];

const NAV_INSIGHT: NavItem[] = [
  { to: "/reports", label: "Reports", icon: FileBarChart2 },
  { to: "/budgets", label: "Budgets", icon: Wallet },
  { to: "/ai-spend", label: "AI spend", icon: Cpu },
  { to: "/adoption", label: "Adoption", icon: Trophy },
];

const NAV_SYSTEM: NavItem[] = [
  { to: "/wiki", label: "Wiki", icon: BookOpen },
  { to: "/onboarding", label: "Onboarding", icon: Sparkles },
];

function NavGroup({ label, items, locationPath, reviewCount }: { label: string; items: NavItem[]; locationPath: string; reviewCount: number }) {
  return (
    <div className="space-y-0.5">
      <div className="px-2.5 pb-1 pt-3 text-[10px] font-medium uppercase tracking-[0.14em] text-muted-foreground/70">
        {label}
      </div>
      {items.map((item) => {
        const active = item.to === "/" ? locationPath === "/" : locationPath.startsWith(item.to);
        const Icon = item.icon;
        return (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === "/"}
            className={cn(
              "group relative flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sec transition-colors",
              active
                ? "bg-sidebar-accent text-foreground font-medium"
                : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground",
            )}
          >
            {active && <span className="absolute left-0 top-1/2 h-4 w-0.5 -translate-y-1/2 rounded-r bg-primary" />}
            <Icon className={cn("h-4 w-4 shrink-0", active ? "text-primary" : "text-muted-foreground/80")} />
            <span className="flex-1">{item.label}</span>
            {item.to === "/review" && reviewCount > 0 && (
              <span className="rounded-sm bg-warning/15 px-1.5 py-0.5 text-meta font-medium tabular-nums text-warning">
                {reviewCount}
              </span>
            )}
          </NavLink>
        );
      })}
    </div>
  );
}

export function AgnesSidebar() {
  const conn = useDashboard((s) => s.conn);
  const reviewCount = useDashboard((s) => s.reviewIds.size);
  const location = useLocation();

  return (
    <aside className="flex h-screen w-[232px] shrink-0 flex-col border-r border-border bg-sidebar">
      {/* Brand */}
      <div className="flex h-16 items-center gap-2.5 px-4">
        <div className="relative flex h-8 w-8 items-center justify-center overflow-hidden rounded-md bg-prism">
          <span className="font-mono text-base font-semibold text-primary-foreground">A</span>
          <div className="pointer-events-none absolute inset-0 bg-gradient-to-br from-white/20 to-transparent" />
        </div>
        <div className="leading-tight">
          <div className="text-[15px] font-semibold tracking-tight">Agnes</div>
          <div className="text-[10px] uppercase tracking-[0.16em] text-muted-foreground">Autonomous CFO</div>
        </div>
      </div>

      <nav className="flex-1 overflow-y-auto px-2 pb-3">
        <NavGroup label="Today" items={NAV_PRIMARY} locationPath={location.pathname} reviewCount={reviewCount} />
        <NavGroup label="Books" items={NAV_FINANCE} locationPath={location.pathname} reviewCount={reviewCount} />
        <NavGroup label="Intelligence" items={NAV_INSIGHT} locationPath={location.pathname} reviewCount={reviewCount} />
        <NavGroup label="System" items={NAV_SYSTEM} locationPath={location.pathname} reviewCount={reviewCount} />
      </nav>

      <div className="border-t border-border px-4 py-3">
        <div className="flex items-center justify-between text-[11px] text-muted-foreground">
          <div className="flex items-center gap-2">
            <LiveDot status={conn} />
          </div>
          <span className="font-mono">v0.2.0</span>
        </div>
      </div>
    </aside>
  );
}
