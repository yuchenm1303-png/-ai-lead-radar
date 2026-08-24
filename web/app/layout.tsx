import type { Metadata } from "next";
import "./globals.css";
import "./controls.css";

export const metadata: Metadata = {
  title: "AI Lead Radar",
  description: "AI 接单雷达：发现最新公开开发需求并进行智能筛选。",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
