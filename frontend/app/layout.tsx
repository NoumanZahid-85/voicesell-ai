import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import { MotionProvider } from "./components/motion-provider";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

const jetbrains = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains",
  display: "swap",
});

export const metadata: Metadata = {
  title: "CALLIOPE — Voice-Powered Sales Assistant",
  description:
    "Talk to an AI sales agent by voice or text. Get instant product answers, place orders, and get personalised recommendations — all in under 500ms.",
  openGraph: {
    title: "CALLIOPE",
    description: "Voice-powered e-commerce AI agent with RAG-grounded answers",
    type: "website",
  },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body
        suppressHydrationWarning
        className={`${inter.variable} ${jetbrains.variable}`}
      >
        <div className="stage" aria-hidden="true" />
        <MotionProvider>{children}</MotionProvider>
      </body>
    </html>
  );
}
