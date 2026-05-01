import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AppLayout } from "@/components/fingent/AppLayout";
import { TraceDrawerProvider } from "@/components/fingent/TraceDrawerContext";
import TodayPage from "./pages/TodayPage";
import LedgerPage from "./pages/LedgerPage";
import MePage from "./pages/MePage";
import ReportsPage from "./pages/ReportsPage";
import AiSpendPage from "./pages/AiSpendPage";
import BudgetsPage from "./pages/BudgetsPage";
import RunsPage from "./pages/RunsPage";
import RunDagPage from "./pages/RunDagPage";
import WikiPage from "./pages/WikiPage";
import ReviewPage from "./pages/ReviewPage";
import AdoptionPage from "./pages/AdoptionPage";
import { ComingSoonPage } from "./pages/ComingSoonPage";
import NotFound from "./pages/NotFound";

const queryClient = new QueryClient();

const App = () => (
  <QueryClientProvider client={queryClient}>
    <TooltipProvider>
      <Toaster />
      <Sonner />
      <BrowserRouter>
        <TraceDrawerProvider>
          <Routes>
            <Route element={<AppLayout />}>
              <Route path="/" element={<TodayPage />} />
              <Route path="/me" element={<MePage />} />
              <Route path="/employees/:id" element={<MePage asAdmin />} />
              <Route path="/ledger" element={<LedgerPage />} />
              <Route path="/budgets" element={<BudgetsPage />} />
              <Route path="/reports" element={<ReportsPage />} />
              <Route path="/ai-spend" element={<AiSpendPage />} />
              <Route path="/review" element={<ReviewPage />} />
              <Route path="/runs" element={<RunsPage />} />
              <Route path="/runs/:id" element={<RunDagPage />} />
              <Route path="/runs/:id/dag" element={<RunDagPage />} />
              <Route path="/wiki" element={<WikiPage />} />
              <Route path="/adoption" element={<AdoptionPage />} />
              <Route path="/onboarding" element={<ComingSoonPage title="Onboarding" hint="8-step CFO setup wizard." />} />
            </Route>
            <Route path="*" element={<NotFound />} />
          </Routes>
        </TraceDrawerProvider>
      </BrowserRouter>
    </TooltipProvider>
  </QueryClientProvider>
);

export default App;
