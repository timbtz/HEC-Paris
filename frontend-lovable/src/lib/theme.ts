import { useEffect } from "react";

type Theme = "dark" | "light";

export function applyTheme(theme: Theme) {
  // Dark is default; toggle the .light class to opt into light mode.
  document.documentElement.classList.toggle("light", theme === "light");
  document.documentElement.classList.toggle("dark", theme === "dark");
  localStorage.setItem("fingent:theme", theme);
}

export function getTheme(): Theme {
  const stored = localStorage.getItem("fingent:theme") as Theme | null;
  if (stored === "light" || stored === "dark") return stored;
  return "dark"; // dark is the canonical Fingent look
}

export function useThemeBootstrap() {
  useEffect(() => {
    applyTheme(getTheme());
  }, []);
}
