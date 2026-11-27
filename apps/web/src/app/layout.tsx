import type { Metadata } from "next";
import { Barlow_Semi_Condensed, JetBrains_Mono } from "next/font/google";
import { Toaster } from "@/components/ui/sonner";
import "./globals.css";

const barlow = Barlow_Semi_Condensed({
  variable: "--font-barlow",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
});
const jetbrains = JetBrains_Mono({ variable: "--font-jetbrains", subsets: ["latin"], weight: ["400", "600"] });

export const metadata: Metadata = {
  title: "Seshat",
  description: "Track every feature flag from creation to cleanup.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${barlow.variable} ${jetbrains.variable}`}>
      <body className="min-h-dvh antialiased">
        {children}
        <Toaster position="bottom-right" />
      </body>
    </html>
  );
}
