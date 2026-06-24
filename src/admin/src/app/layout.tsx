import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "本体助手管理平台",
  description: "Ontology Assistant Admin Panel",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="antialiased">{children}</body>
    </html>
  );
}
