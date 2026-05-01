import { useEffect } from "react";
import { Outlet } from "react-router-dom";
import { FingentSidebar } from "./FingentSidebar";
import { FingentTopBar } from "./FingentTopBar";
import { TraceDrawer } from "./TraceDrawer";
import { useThemeBootstrap } from "@/lib/theme";
import { useDashboard } from "@/store/dashboard";
import { useSSE } from "@/hooks/useSSE";
import { fetchEnvelopes, fetchJournalEntries } from "@/lib/endpoints";
import { dashboardMockGenerator } from "@/lib/sseGenerators";

export function AppLayout() {
  useThemeBootstrap();

  const setEntries = useDashboard((s) => s.setEntries);
  const setEnvelopes = useDashboard((s) => s.setEnvelopes);
  const setConn = useDashboard((s) => s.setConn);
  const setMocked = useDashboard((s) => s.setMocked);
  const applySseEvent = useDashboard((s) => s.applySseEvent);
  const envelopes = useDashboard((s) => s.envelopes);

  // Initial load — entries + envelopes
  useEffect(() => {
    fetchJournalEntries({ limit: 50 })
      .then((res) => {
        setEntries(res.data.items);
        setMocked(res.mocked);
      })
      .catch(() => {});
    fetchEnvelopes()
      .then((res) => setEnvelopes(res.data.items))
      .catch(() => {});
  }, [setEntries, setEnvelopes, setMocked]);

  // Dashboard SSE — once on app boot
  const { status } = useSSE({
    url: "/dashboard/stream",
    onEvent: applySseEvent,
    mockGenerator: dashboardMockGenerator(() => Object.keys(envelopes).map(Number)),
  });

  useEffect(() => setConn(status), [status, setConn]);

  return (
    <div className="flex h-screen w-full overflow-hidden bg-background text-foreground">
      <FingentSidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <FingentTopBar />
        <main className="flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
      <TraceDrawer />
    </div>
  );
}
