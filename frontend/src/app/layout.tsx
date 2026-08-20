import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "VaaniReach",
  description: "Multilingual Outreach Video Generator — architecture phase",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
