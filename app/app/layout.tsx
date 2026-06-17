import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  weight: ["300", "400", "500", "600", "700", "800"],
  variable: "--font-inter",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Ghost Requirement Agent",
  description:
    "Autonomous pipeline for capturing undocumented product requirements from Slack and Teams, reconciling them against your engineering backlog.",
  keywords: ["requirements", "product management", "AI", "Slack", "Jira"],
  authors: [{ name: "Ghost Requirement Agent" }],
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={`${inter.variable} h-full`}>
      <body className="min-h-full flex flex-col bg-mesh antialiased">
        {children}
      </body>
    </html>
  );
}
