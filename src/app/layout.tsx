import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Coding Plan 对比 - 国内主流平台横评",
  description:
    "一站式对比小米 MiMo、智谱 GLM、MiniMax、Kimi、火山引擎、阿里云百炼、腾讯云、白云智算等国内主流 AI Coding Plan 套餐。价格、模型、用量限制全面横评。",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
