import { useState } from "react";
import { Plug, Copy, Check, ExternalLink } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

const CLAUDE_DESKTOP_SNIPPET = `{
  "mcpServers": {
    "agnes": {
      "command": "python",
      "args": ["-m", "backend.mcp"],
      "cwd": "/absolute/path/to/agnes"
    }
  }
}`;

const CLAUDE_CODE_SNIPPET = `# from the agnes repo
uv sync --extra mcp
claude mcp add agnes -- python -m backend.mcp`;

const HTTP_SERVER_SNIPPET = `# 1. start the HTTP transport in a long-running shell
uv sync --extra mcp
python -m backend.mcp --http 127.0.0.1:8765`;

const HTTP_CLIENT_SNIPPET = `{
  "mcpServers": {
    "agnes": {
      "url": "http://127.0.0.1:8765/mcp"
    }
  }
}`;

const TOOLS_PREVIEW: { name: string; pitch: string }[] = [
  { name: "run_pipeline", pitch: "Kick off any of 7 pipelines from chat (period_close, vat_return, …)" },
  { name: "list_journal_entries", pitch: "Query the live ledger with filters (period, status, basis)" },
  { name: "get_report", pitch: "Trial balance, balance sheet, income statement, cashflow, VAT" },
  { name: "approve_period_report", pitch: "Close a period from your AI agent — fully audited" },
  { name: "get_wiki_page", pitch: "Read the Living Rule Wiki (FR-PCG, dinners, SaaS catalogue)" },
  { name: "simulate_swan_event", pitch: "Replay a Swan transaction for live demos" },
];

const RESOURCES_PREVIEW = [
  "agnes://run/{run_id}",
  "agnes://entry/{entry_id}/trace",
  "agnes://employee/{employee_id}",
  "agnes://wiki/{page_id}",
  "agnes://period_report/{report_id}",
];

export function MCPConnectButton() {
  return (
    <Dialog>
      <DialogTrigger asChild>
        <button
          type="button"
          className="inline-flex items-center gap-1.5 rounded-md border border-primary/30 bg-prism/10 px-2.5 py-1.5 text-sec font-medium text-primary transition-colors hover:bg-prism/15"
          title="Connect Agnes to Claude Desktop, Claude Code, or any MCP client"
        >
          <Plug className="h-3.5 w-3.5" />
          <span className="hidden md:inline">MCP</span>
        </button>
      </DialogTrigger>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Plug className="h-4 w-4 text-primary" />
            Connect Agnes via MCP
          </DialogTitle>
          <DialogDescription>
            Open Agnes' agentic surface to Claude Desktop, Claude Code, or any
            MCP-compatible AI client. One config — your AI agent can now run
            pipelines, query the ledger, approve closes, and read the policy wiki.
          </DialogDescription>
        </DialogHeader>

        <Tabs defaultValue="desktop" className="mt-2">
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="desktop">Claude Desktop</TabsTrigger>
            <TabsTrigger value="code">Claude Code</TabsTrigger>
            <TabsTrigger value="http">HTTP / remote</TabsTrigger>
          </TabsList>

          <TabsContent value="desktop" className="mt-3 space-y-3">
            <Steps
              items={[
                <>Install the MCP extra: <Inline>uv sync --extra mcp</Inline></>,
                <>
                  Open <Inline>~/Library/Application Support/Claude/claude_desktop_config.json</Inline>
                  {" "}(macOS) or <Inline>%APPDATA%\Claude\claude_desktop_config.json</Inline> (Windows).
                </>,
                <>Paste the snippet below. Replace the <Inline>cwd</Inline> with your absolute repo path.</>,
                <>Restart Claude Desktop. Look for the 🔌 icon — Agnes will appear as an available toolset.</>,
              ]}
            />
            <CodeBlock label="claude_desktop_config.json" code={CLAUDE_DESKTOP_SNIPPET} />
          </TabsContent>

          <TabsContent value="code" className="mt-3 space-y-3">
            <Steps
              items={[
                <>Install the MCP extra and register Agnes with Claude Code:</>,
                <>The CLI will auto-detect tools on next launch — try <Inline>claude</Inline> from the repo root.</>,
              ]}
            />
            <CodeBlock label="terminal" code={CLAUDE_CODE_SNIPPET} />
          </TabsContent>

          <TabsContent value="http" className="mt-3 space-y-3">
            <Steps
              items={[
                <>Run the streamable-http transport (e.g. on a shared dev box or container):</>,
                <>Point any MCP client at the URL — works for remote agents and self-hosted setups.</>,
              ]}
            />
            <CodeBlock label="server" code={HTTP_SERVER_SNIPPET} />
            <CodeBlock label="client config" code={HTTP_CLIENT_SNIPPET} />
          </TabsContent>
        </Tabs>

        <div className="mt-4 space-y-2 rounded-md border border-border bg-muted/30 p-3">
          <div className="text-meta font-medium uppercase tracking-[0.14em] text-muted-foreground">
            What your agent gets
          </div>
          <ul className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
            {TOOLS_PREVIEW.map((t) => (
              <li key={t.name} className="text-sec">
                <span className="font-mono text-meta text-primary">{t.name}</span>
                <span className="ml-1.5 text-muted-foreground">— {t.pitch}</span>
              </li>
            ))}
          </ul>
          <div className="pt-1 text-meta text-muted-foreground">
            Plus pinnable resources:{" "}
            {RESOURCES_PREVIEW.map((r, i) => (
              <span key={r}>
                <span className="font-mono text-foreground/80">{r}</span>
                {i < RESOURCES_PREVIEW.length - 1 ? <span className="px-1">·</span> : null}
              </span>
            ))}
          </div>
        </div>

        <div className="mt-2 flex items-center justify-between text-meta text-muted-foreground">
          <span>20 tools · 6 resources · 3 prompts</span>
          <a
            href="https://modelcontextprotocol.io/"
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 hover:text-foreground"
          >
            Learn about MCP <ExternalLink className="h-3 w-3" />
          </a>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function Steps({ items }: { items: React.ReactNode[] }) {
  return (
    <ol className="space-y-1.5 text-sec">
      {items.map((item, i) => (
        <li key={i} className="flex gap-2">
          <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-prism/15 font-mono text-[10px] font-semibold text-primary">
            {i + 1}
          </span>
          <span className="text-foreground/90">{item}</span>
        </li>
      ))}
    </ol>
  );
}

function Inline({ children }: { children: React.ReactNode }) {
  return (
    <code className="rounded-sm bg-muted px-1 py-0.5 font-mono text-meta text-foreground">
      {children}
    </code>
  );
}

function CodeBlock({ label, code }: { label: string; code: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard can fail in non-secure contexts; fall through silently.
    }
  }

  return (
    <div className="overflow-hidden rounded-md border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border bg-muted/40 px-3 py-1.5">
        <span className="text-meta font-mono uppercase tracking-wide text-muted-foreground">
          {label}
        </span>
        <button
          type="button"
          onClick={copy}
          className={cn(
            "inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-meta transition-colors",
            copied ? "text-positive" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="overflow-x-auto p-3 text-meta leading-relaxed">
        <code className="font-mono text-foreground/90">{code}</code>
      </pre>
    </div>
  );
}
