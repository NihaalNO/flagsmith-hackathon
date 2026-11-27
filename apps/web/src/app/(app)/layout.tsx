import { SessionProvider } from "@/components/seshat/session";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return <SessionProvider>{children}</SessionProvider>;
}
