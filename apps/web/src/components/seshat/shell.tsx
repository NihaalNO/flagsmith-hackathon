"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChevronsUpDown, LogOut } from "lucide-react";
import { TextRoll } from "@/components/ui/skiper-ui/skiper58";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Wordmark } from "./gates";
import { useSession } from "./session";

const NAV = [
  { href: "/", label: "Board" },
  { href: "/experiments", label: "Experiments" },
  { href: "/repositories", label: "Repositories" },
  { href: "/connections", label: "Connections" },
  { href: "/activity", label: "Activity" },
];

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { session, list, workspace, chooseWorkspace, signOut } = useSession();
  const login = (session?.user.user_metadata?.user_name as string | undefined) ?? session?.user.email;

  return (
    <div className="flex min-h-dvh flex-col md:flex-row">
      <aside className="flex shrink-0 flex-col gap-6 border-b border-sidebar-border bg-sidebar px-5 py-5
                        md:sticky md:top-0 md:h-dvh md:w-56 md:border-b-0 md:border-r">
        <Wordmark className="text-2xl" />

        <DropdownMenu>
          <DropdownMenuTrigger className="flex w-full items-center justify-between rounded-md border border-border
                                          px-3 py-2 text-left hover:bg-accent">
            <span className="min-w-0">
              <span className="block text-xs text-muted-foreground">Workspace</span>
              <span className="block truncate font-medium">{workspace?.name}</span>
            </span>
            <ChevronsUpDown className="size-4 text-muted-foreground" aria-hidden />
          </DropdownMenuTrigger>
          <DropdownMenuContent className="w-52">
            {list?.workspaces.map((w) => (
              <DropdownMenuItem key={w.id} onClick={() => chooseWorkspace(w.id)}>
                {w.name}
              </DropdownMenuItem>
            ))}
            {list?.install_url && (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={() => window.open(list.install_url!, "_blank")}>
                  Add a GitHub account or organization
                </DropdownMenuItem>
              </>
            )}
          </DropdownMenuContent>
        </DropdownMenu>

        <nav aria-label="Main" className="flex gap-5 overflow-x-auto md:flex-col md:gap-3">
          {NAV.map((item) => {
            const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
            return (
              <Link key={item.href} href={item.href} aria-current={active ? "page" : undefined}
                className={`text-xl font-semibold ${active ? "text-foreground" : "text-muted-foreground hover:text-foreground"}`}>
                <TextRoll>{item.label}</TextRoll>
              </Link>
            );
          })}
        </nav>

        <div className="mt-auto hidden space-y-3 text-sm md:block">
          <div className="flex items-center justify-between gap-2">
            <span className="truncate text-muted-foreground">{login}</span>
            <button onClick={signOut} className="rounded p-1 text-muted-foreground hover:text-foreground"
              aria-label="Sign out" title="Sign out">
              <LogOut className="size-4" />
            </button>
          </div>
          <a href="https://skiper-ui.com" target="_blank" rel="noreferrer"
            className="block text-xs text-muted-foreground/70 hover:text-muted-foreground">
            Motion components by Skiper UI
          </a>
        </div>
      </aside>
      <main className="min-w-0 flex-1">{children}</main>
    </div>
  );
}
