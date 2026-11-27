import React from "react";
import { useFlags, useFlagsmith } from "flagsmith/react";
import { ThemeToggle } from "./ThemeToggle";

export function Header() {
  const { dark_mode } = useFlags(["dark_mode"], ["plan"]);
  const flagsmith = useFlagsmith();
  const plan = flagsmith.getTrait("plan");
  return (
    <header>
      <span>{plan}</span>
      {dark_mode.enabled && <ThemeToggle />}
    </header>
  );
}
