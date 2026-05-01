import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Award, Coins, Flame, Minus, Plus, Sparkles, ThumbsDown, ThumbsUp, Trophy, Wand2 } from "lucide-react";
import {
  approveGamificationCompletion,
  fetchGamificationCoinAdjustments,
  fetchGamificationCompletions,
  fetchGamificationLeaderboard,
  fetchGamificationRewards,
  fetchGamificationTasks,
  fetchGamificationToday,
  rejectGamificationCompletion,
  submitGamificationCoinAdjustment,
  submitGamificationCompletion,
  submitGamificationRedemption,
} from "@/lib/endpoints";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/hooks/use-toast";
import { cn } from "@/lib/utils";
import type {
  GamificationCoinAdjustment,
  GamificationCompletion,
  GamificationLeaderboardRow,
  GamificationReward,
  GamificationTask,
  GamificationToday,
} from "@/lib/types";

// Default acting identity. The header `x-fingent-author` is the auth seam —
// Tim is seeded as a manager so this gives the demo a manager view out of
// the box. Switch via the small input top-right to test as Marie/Paul.
const DEFAULT_AUTHOR = "tim@hec.example";

// Mirror the backend constant. Used for the "X coins per agent call"
// callout on the leaderboard.
const AUTO_COIN_REWARD = 5;

// Demo employee ids match the audit/0002 seed: 1=Tim, 2=Marie, 3=Paul.
// The Today view is a single-employee widget — pick from the same picker
// the manager uses to switch identity.
const EMPLOYEE_BY_EMAIL: Record<string, number> = {
  "tim@hec.example": 1,
  "marie@hec.example": 2,
  "paul@hec.example": 3,
};

export default function AdoptionPage() {
  const [author, setAuthor] = useState(DEFAULT_AUTHOR);
  const employeeId = EMPLOYEE_BY_EMAIL[author] ?? 1;

  return (
    <div className="mx-auto max-w-[1400px] space-y-5 px-6 py-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Adoption</h1>
          <p className="text-sec text-muted-foreground">
            Coins, leaderboard, rewards. Every real agent call auto-credits the
            attributed employee — manual completions cover AI use that bypasses
            Fingent (Claude desktop, ChatGPT browser, …).
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-meta uppercase tracking-[0.16em] text-muted-foreground">
            Acting as
          </span>
          <Input
            value={author}
            onChange={(e) => setAuthor(e.target.value)}
            className="h-8 w-[220px] font-mono text-xs"
          />
        </div>
      </header>

      <Tabs defaultValue="today" className="space-y-4">
        <TabsList>
          <TabsTrigger value="today">Today</TabsTrigger>
          <TabsTrigger value="leaderboard">Leaderboard</TabsTrigger>
          <TabsTrigger value="library">Task library</TabsTrigger>
          <TabsTrigger value="rewards">Rewards</TabsTrigger>
          <TabsTrigger value="queue">Manager queue</TabsTrigger>
        </TabsList>

        <TabsContent value="today">
          <TodayView employeeId={employeeId} />
        </TabsContent>
        <TabsContent value="leaderboard">
          <LeaderboardView />
        </TabsContent>
        <TabsContent value="library">
          <LibraryView author={author} />
        </TabsContent>
        <TabsContent value="rewards">
          <RewardsView author={author} employeeId={employeeId} />
        </TabsContent>
        <TabsContent value="queue">
          <QueueView author={author} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Today
// ──────────────────────────────────────────────────────────────────────────

function TodayView({ employeeId }: { employeeId: number }) {
  const { data } = useQuery({
    queryKey: ["gamification-today", employeeId],
    queryFn: () => fetchGamificationToday(employeeId).then((r) => r.data),
    refetchInterval: 5000,
  });

  if (!data) return <Skeleton />;
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
      <StatCard
        icon={<Coins className="h-4 w-4 text-coin" />}
        label="Coins today"
        value={data.coins_today}
        sub={`${data.completions_today} approved completions`}
      >
        <Progress
          value={Math.min(100, (data.coins_today / data.daily_target) * 100)}
          className="mt-3 h-1.5"
        />
        <p className="mt-1 text-meta text-muted-foreground">
          Target {data.daily_target} coins/day
        </p>
      </StatCard>
      <StatCard
        icon={<Flame className="h-4 w-4 text-streak" />}
        label="Streak"
        value={`${data.streak_days} day${data.streak_days === 1 ? "" : "s"}`}
        sub="Consecutive days with ≥1 approved completion"
      >
        <StreakStrip history={data.daily_history} />
        <StreakCallout days={data.streak_days} />
      </StatCard>
      <StatCard
        icon={<Sparkles className="h-4 w-4" />}
        label="Coin balance"
        value={data.coins_balance}
        sub="Spendable coins (locked redemptions excluded)"
      />
    </div>
  );
}

function StreakStrip({ history }: { history: GamificationToday["daily_history"] }) {
  // Show last 14 boxes — date order ascending, today on the right.
  const today = new Date().toISOString().slice(0, 10);
  const map = new Map(history.map((h) => [h.date, h.completions]));
  const cells = useMemo(() => {
    const out: Array<{ date: string; n: number }> = [];
    for (let i = 13; i >= 0; i--) {
      const d = new Date();
      d.setDate(d.getDate() - i);
      const iso = d.toISOString().slice(0, 10);
      out.push({ date: iso, n: map.get(iso) ?? 0 });
    }
    return out;
  }, [history]);
  return (
    <div className="mt-3 flex gap-1">
      {cells.map((c) => (
        <div
          key={c.date}
          title={`${c.date} — ${c.n} completion${c.n === 1 ? "" : "s"}`}
          className={cn(
            "h-4 w-4 rounded-sm",
            c.n === 0
              ? "bg-muted"
              : c.n < 3
                ? "bg-streak/40"
                : c.n < 8
                  ? "bg-streak/70"
                  : "bg-streak",
            c.date === today && "ring-1 ring-streak",
          )}
        />
      ))}
    </div>
  );
}

function StreakCallout({ days }: { days: number }) {
  if (days <= 0) return null;
  return (
    <p className="mt-2 text-meta text-streak">
      🔥 You're on a {days}-day streak — keep it up!
    </p>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Leaderboard
// ──────────────────────────────────────────────────────────────────────────

function LeaderboardView() {
  const [period, setPeriod] = useState<"week" | "month" | "all">("month");
  const { data } = useQuery({
    queryKey: ["gamification-leaderboard", period],
    queryFn: () => fetchGamificationLeaderboard(period).then((r) => r.data),
    refetchInterval: 5000,
  });

  const items = data?.items ?? [];
  const allZero = items.length === 0 || items.every((r) => r.coins === 0);

  // Department roll-up — client-side aggregation over the same rows.
  // Mirrors pulse-ai-grow Leaderboard.tsx's second card.
  const deptRows = useMemo(() => {
    const map = new Map<string, { dept: string; coins: number; calls: number; people: number }>();
    for (const r of items) {
      if (r.coins === 0 && r.call_count === 0) continue;
      const dept = r.department && r.department.length > 0 ? r.department : "Unassigned";
      const acc = map.get(dept) ?? { dept, coins: 0, calls: 0, people: 0 };
      acc.coins += r.coins;
      acc.calls += r.call_count;
      acc.people += 1;
      map.set(dept, acc);
    }
    return Array.from(map.values()).sort((a, b) => b.coins - a.coins);
  }, [items]);

  const periodLabel =
    period === "all" ? "all time" : period === "week" ? "this week" : "this month";

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <div>
            <CardTitle className="flex items-center gap-2">
              <Trophy className="h-4 w-4 text-coin" />
              {period === "month"
                ? "This month's top AI adopters"
                : `Top AI adopters · ${periodLabel}`}
            </CardTitle>
            <p className="mt-1 text-meta text-muted-foreground">Updated in real time.</p>
          </div>
          <div className="flex gap-1">
            {(["week", "month", "all"] as const).map((p) => (
              <Button
                key={p}
                size="sm"
                variant={period === p ? "default" : "ghost"}
                onClick={() => setPeriod(p)}
              >
                {p === "all" ? "All time" : `This ${p}`}
              </Button>
            ))}
          </div>
        </CardHeader>
        <CardContent>
          <p className="mb-3 text-meta text-muted-foreground">
            Auto-credit: {data?.auto_coin_reward ?? AUTO_COIN_REWARD} coins per
            attributed agent call. Manual entries are manager-approved.
          </p>
          <table className="w-full text-sec">
            <thead className="text-meta uppercase tracking-[0.12em] text-muted-foreground">
              <tr className="border-b border-border">
                <th className="py-2 text-left font-medium">#</th>
                <th className="py-2 text-left font-medium">Employee</th>
                <th className="py-2 text-left font-medium">Dept</th>
                <th className="py-2 text-right font-medium">Calls</th>
                <th className="py-2 text-right font-medium">From agents</th>
                <th className="py-2 text-right font-medium">Manual</th>
                <th className="py-2 text-right font-medium">Adjustments</th>
                <th className="py-2 text-right font-medium">Total coins</th>
              </tr>
            </thead>
            <tbody>
              {!allZero &&
                items.map((row: GamificationLeaderboardRow, i: number) => (
                  <tr key={row.employee_id} className="border-b border-border/50">
                    <td className="py-2 font-mono font-bold text-primary">{i + 1}</td>
                    <td className="py-2">{row.full_name ?? row.email}</td>
                    <td className="py-2 text-muted-foreground">{row.department ?? "—"}</td>
                    <td className="py-2 text-right tabular-nums">{row.call_count}</td>
                    <td className="py-2 text-right tabular-nums">{row.earned_auto}</td>
                    <td className="py-2 text-right tabular-nums">{row.earned_manual}</td>
                    <td className="py-2 text-right tabular-nums">{row.adjustments}</td>
                    <td className="py-2 text-right font-semibold tabular-nums text-coin">
                      {row.coins}
                    </td>
                  </tr>
                ))}
              {allZero && (
                <tr>
                  <td colSpan={8} className="py-6 text-center text-muted-foreground">
                    No data yet {periodLabel}.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Trophy className="h-4 w-4 text-coin" />
            By department
          </CardTitle>
          <p className="mt-1 text-meta text-muted-foreground">
            Coin totals rolled up by department · {periodLabel}.
          </p>
        </CardHeader>
        <CardContent>
          {deptRows.length === 0 ? (
            <p className="py-3 text-center text-meta text-muted-foreground">
              No department coins yet {periodLabel}.
            </p>
          ) : (
            <table className="w-full text-sec">
              <thead className="text-meta uppercase tracking-[0.12em] text-muted-foreground">
                <tr className="border-b border-border">
                  <th className="py-2 text-left font-medium">#</th>
                  <th className="py-2 text-left font-medium">Department</th>
                  <th className="py-2 text-right font-medium">People</th>
                  <th className="py-2 text-right font-medium">Calls</th>
                  <th className="py-2 text-right font-medium">Coins</th>
                </tr>
              </thead>
              <tbody>
                {deptRows.map((d, i) => (
                  <tr key={d.dept} className="border-b border-border/50">
                    <td className="py-2 font-mono font-bold text-primary">{i + 1}</td>
                    <td className="py-2">{d.dept}</td>
                    <td className="py-2 text-right tabular-nums text-muted-foreground">
                      {d.people}
                    </td>
                    <td className="py-2 text-right tabular-nums">{d.calls}</td>
                    <td className="py-2 text-right font-semibold tabular-nums text-coin">
                      {d.coins}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Task library (browse + self-declare)
// ──────────────────────────────────────────────────────────────────────────

function LibraryView({ author }: { author: string }) {
  const qc = useQueryClient();
  const [dept, setDept] = useState<string>("All");
  const { data } = useQuery({
    queryKey: ["gamification-tasks"],
    queryFn: () => fetchGamificationTasks().then((r) => r.data),
  });

  const submit = useMutation({
    mutationFn: ({ taskId, note }: { taskId: number; note?: string }) =>
      submitGamificationCompletion(taskId, note, author).then((r) => r.data),
    onSuccess: () => {
      toast({ title: "Submitted", description: "Manager will review." });
      qc.invalidateQueries({ queryKey: ["gamification-completions"] });
    },
    onError: (e: Error) => toast({ title: "Failed", description: e.message, variant: "destructive" }),
  });

  const tasks = data?.items ?? [];
  const departments = useMemo(
    () => ["All", ...Array.from(new Set(tasks.map((t) => t.department))).sort()],
    [tasks],
  );
  const filtered = dept === "All" ? tasks : tasks.filter((t) => t.department === dept);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Wand2 className="h-4 w-4" />
          Task library
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap gap-2">
          {departments.map((d) => (
            <Button
              key={d}
              size="sm"
              variant={dept === d ? "default" : "outline"}
              onClick={() => setDept(d)}
            >
              {d}
            </Button>
          ))}
        </div>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {filtered.map((t: GamificationTask) => (
            <div
              key={t.id}
              className="rounded-lg border border-border p-4 hover:bg-muted/30"
            >
              <div className="mb-1 flex items-center justify-between gap-2">
                <h3 className="font-medium">{t.title}</h3>
                <Badge variant="secondary">{t.coin_value} coins</Badge>
              </div>
              <p className="mb-3 text-sec text-muted-foreground">{t.description}</p>
              <div className="flex items-center justify-between">
                <Badge variant="outline">{t.department}</Badge>
                <Button
                  size="sm"
                  disabled={submit.isPending}
                  onClick={() => submit.mutate({ taskId: t.id })}
                >
                  Mark as done
                </Button>
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Rewards
// ──────────────────────────────────────────────────────────────────────────

function RewardsView({ author, employeeId }: { author: string; employeeId: number }) {
  const qc = useQueryClient();
  const { data: rewardsData } = useQuery({
    queryKey: ["gamification-rewards"],
    queryFn: () => fetchGamificationRewards().then((r) => r.data),
  });
  const { data: today } = useQuery({
    queryKey: ["gamification-today", employeeId],
    queryFn: () => fetchGamificationToday(employeeId).then((r) => r.data),
  });

  const redeem = useMutation({
    mutationFn: (rewardId: number) =>
      submitGamificationRedemption(rewardId, author).then((r) => r.data),
    onSuccess: (out) => {
      toast({
        title: "Redemption pending",
        description: `Manager will approve. ${out.coin_cost} coins locked.`,
      });
      qc.invalidateQueries({ queryKey: ["gamification-today"] });
    },
    onError: (e: Error) =>
      toast({ title: "Failed", description: e.message, variant: "destructive" }),
  });

  const balance = today?.coins_balance ?? 0;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Award className="h-4 w-4" />
          Rewards · balance {balance} coins
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
          {rewardsData?.items.map((r: GamificationReward) => {
            const affordable = balance >= r.coin_cost;
            return (
              <div key={r.id} className="rounded-lg border border-border p-4">
                <div className="mb-2 text-2xl">{r.emoji}</div>
                <h3 className="mb-1 font-medium">{r.name}</h3>
                <p className="mb-3 text-sec text-muted-foreground">
                  {r.description}
                </p>
                <div className="flex items-center justify-between">
                  <Badge variant="secondary">{r.coin_cost} coins</Badge>
                  <Button
                    size="sm"
                    disabled={!affordable || redeem.isPending}
                    onClick={() => redeem.mutate(r.id)}
                  >
                    {affordable ? "Redeem" : "Need more"}
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Manager queue
// ──────────────────────────────────────────────────────────────────────────

function QueueView({ author }: { author: string }) {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["gamification-completions", "pending"],
    queryFn: () =>
      fetchGamificationCompletions({ status: "pending", source: "manual" }).then(
        (r) => r.data,
      ),
    refetchInterval: 4000,
  });

  const approve = useMutation({
    mutationFn: (id: number) =>
      approveGamificationCompletion(id, author).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["gamification-completions"] });
      qc.invalidateQueries({ queryKey: ["gamification-leaderboard"] });
    },
    onError: (e: Error) =>
      toast({ title: "Failed", description: e.message, variant: "destructive" }),
  });

  const reject = useMutation({
    mutationFn: (id: number) =>
      rejectGamificationCompletion(id, author).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["gamification-completions"] });
    },
    onError: (e: Error) =>
      toast({ title: "Failed", description: e.message, variant: "destructive" }),
  });

  const items = data?.items ?? [];
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>Manager queue · pending manual completions</CardTitle>
          <AdjustCoinsDialog author={author} />
        </CardHeader>
        <CardContent>
          {items.length === 0 ? (
            <p className="text-sec text-muted-foreground">Inbox zero.</p>
          ) : (
            <div className="space-y-2">
              {items.map((c: GamificationCompletion) => (
                <div
                  key={c.id}
                  className="flex items-center justify-between rounded-md border border-border p-3"
                >
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-medium">{c.employee_full_name}</span>
                      <span className="text-muted-foreground">·</span>
                      <span className="text-sec text-muted-foreground">
                        {c.task_title}
                      </span>
                    </div>
                    {c.note && (
                      <p className="mt-1 text-meta text-muted-foreground">"{c.note}"</p>
                    )}
                  </div>
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => reject.mutate(c.id)}
                      disabled={reject.isPending}
                    >
                      <ThumbsDown className="mr-1 h-3.5 w-3.5" />
                      Reject
                    </Button>
                    <Button
                      size="sm"
                      onClick={() => approve.mutate(c.id)}
                      disabled={approve.isPending}
                    >
                      <ThumbsUp className="mr-1 h-3.5 w-3.5" />
                      Approve
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// AdjustCoinsDialog — manager-only ± adjustment with inline last-10 history.
// Lifted from pulse-ai-grow Manage.tsx > AdjustCoinsDialog.
// Backend rejects 403 if `author` isn't a manager — no client-side gate, the
// button is just hidden if the API returns 403 on submit (toast).
// ──────────────────────────────────────────────────────────────────────────

function AdjustCoinsDialog({ author }: { author: string }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [employeeId, setEmployeeId] = useState<number>(2); // default: Marie
  const [mode, setMode] = useState<"add" | "remove">("add");
  const [amount, setAmount] = useState<string>("");
  const [reason, setReason] = useState<string>("");

  const { data: leaderboard } = useQuery({
    queryKey: ["gamification-leaderboard", "all"],
    queryFn: () => fetchGamificationLeaderboard("all").then((r) => r.data),
    enabled: open,
  });

  const { data: history } = useQuery({
    queryKey: ["gamification-coin-adjustments", employeeId],
    queryFn: () =>
      fetchGamificationCoinAdjustments({ employee_id: employeeId, limit: 10 }).then(
        (r) => r.data,
      ),
    enabled: open && employeeId > 0,
    refetchInterval: 4000,
  });

  const submit = useMutation({
    mutationFn: () => {
      const n = Number(amount);
      if (!Number.isFinite(n) || n <= 0) {
        return Promise.reject(new Error("Enter a positive amount"));
      }
      const signed = mode === "add" ? n : -n;
      return submitGamificationCoinAdjustment(
        { employee_id: employeeId, amount: signed, reason: reason || undefined },
        author,
      ).then((r) => r.data);
    },
    onSuccess: (out) => {
      toast({
        title: mode === "add" ? "Coins added" : "Coins removed",
        description: `New balance: ${out.new_balance} coins.`,
      });
      setAmount("");
      setReason("");
      qc.invalidateQueries({ queryKey: ["gamification-coin-adjustments"] });
      qc.invalidateQueries({ queryKey: ["gamification-leaderboard"] });
      qc.invalidateQueries({ queryKey: ["gamification-today"] });
    },
    onError: (e: Error) =>
      toast({ title: "Failed", description: e.message, variant: "destructive" }),
  });

  const employees = leaderboard?.items ?? [];

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline">
          <Coins className="mr-1.5 h-3.5 w-3.5 text-coin" />
          Adjust coins
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Adjust coin balance</DialogTitle>
          <DialogDescription>
            Manager-only signed adjustment. Positive = credit, negative = debit.
            Cannot push balance below zero.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-meta uppercase tracking-[0.12em] text-muted-foreground">
              Employee
            </label>
            <select
              value={employeeId}
              onChange={(e) => setEmployeeId(Number(e.target.value))}
              className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-sec"
            >
              {employees.length === 0 && <option value={0}>(loading…)</option>}
              {employees.map((e) => (
                <option key={e.employee_id} value={e.employee_id}>
                  {e.full_name ?? e.email} · {e.coins} coins
                </option>
              ))}
            </select>
          </div>

          <div className="flex gap-2">
            <Button
              type="button"
              variant={mode === "add" ? "default" : "outline"}
              size="sm"
              className={mode === "add" ? "bg-coin text-coin-foreground hover:bg-coin/90" : ""}
              onClick={() => setMode("add")}
            >
              <Plus className="mr-1 h-3.5 w-3.5" /> Add
            </Button>
            <Button
              type="button"
              variant={mode === "remove" ? "default" : "outline"}
              size="sm"
              className={
                mode === "remove" ? "bg-destructive text-destructive-foreground hover:bg-destructive/90" : ""
              }
              onClick={() => setMode("remove")}
            >
              <Minus className="mr-1 h-3.5 w-3.5" /> Remove
            </Button>
          </div>

          <div>
            <label className="mb-1 block text-meta uppercase tracking-[0.12em] text-muted-foreground">
              Amount
            </label>
            <Input
              type="number"
              min={1}
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              placeholder="e.g. 25"
              className="font-mono"
            />
          </div>

          <div>
            <label className="mb-1 block text-meta uppercase tracking-[0.12em] text-muted-foreground">
              Reason (optional)
            </label>
            <Textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Closed Q1 books two days early"
              maxLength={500}
              rows={2}
            />
          </div>

          <div>
            <div className="mb-2 text-meta uppercase tracking-[0.12em] text-muted-foreground">
              Last 10 adjustments
            </div>
            {history && history.items.length === 0 ? (
              <p className="text-sec text-muted-foreground">No adjustments yet.</p>
            ) : (
              <div className="max-h-48 space-y-1 overflow-y-auto">
                {(history?.items ?? []).map((h: GamificationCoinAdjustment) => (
                  <div
                    key={h.id}
                    className="flex items-center justify-between rounded-md border border-border/60 px-2 py-1.5 text-sec"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span
                          className={cn(
                            "font-mono font-semibold",
                            h.amount > 0 ? "text-coin" : "text-destructive",
                          )}
                        >
                          {h.amount > 0 ? `+${h.amount}` : h.amount}
                        </span>
                        {h.adjusted_by_full_name && (
                          <span className="text-meta text-muted-foreground">
                            by {h.adjusted_by_full_name}
                          </span>
                        )}
                      </div>
                      {h.reason && (
                        <p className="truncate text-meta text-muted-foreground">{h.reason}</p>
                      )}
                    </div>
                    <span className="ml-2 shrink-0 font-mono text-meta text-muted-foreground">
                      {h.created_at.slice(0, 10)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Close
          </Button>
          <Button
            onClick={() => submit.mutate()}
            disabled={submit.isPending || !amount}
          >
            {submit.isPending ? "Submitting…" : "Apply adjustment"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Tiny helpers
// ──────────────────────────────────────────────────────────────────────────

function StatCard({
  icon,
  label,
  value,
  sub,
  children,
}: {
  icon: React.ReactNode;
  label: string;
  value: number | string;
  sub?: string;
  children?: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sec font-medium text-muted-foreground">
          {icon}
          {label}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="display text-3xl tabular-nums">{value}</div>
        {sub && <p className="text-meta text-muted-foreground">{sub}</p>}
        {children}
      </CardContent>
    </Card>
  );
}

function Skeleton() {
  return (
    <div className="grid grid-cols-3 gap-4">
      {[0, 1, 2].map((i) => (
        <div key={i} className="h-32 animate-pulse rounded-lg bg-muted/40" />
      ))}
    </div>
  );
}
