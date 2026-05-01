import { createContext, useContext, useState, type ReactNode } from "react";

interface DrawerCtx {
  openEntryId: number | null;
  open: (entryId: number) => void;
  close: () => void;
}

const Ctx = createContext<DrawerCtx | null>(null);

export function TraceDrawerProvider({ children }: { children: ReactNode }) {
  const [openEntryId, setOpenEntryId] = useState<number | null>(null);
  return (
    <Ctx.Provider
      value={{
        openEntryId,
        open: (id) => setOpenEntryId(id),
        close: () => setOpenEntryId(null),
      }}
    >
      {children}
    </Ctx.Provider>
  );
}

export function useTraceDrawer() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useTraceDrawer must be used inside TraceDrawerProvider");
  return ctx;
}
