"use client";

import { useState } from "react";
import { platforms } from "@/data/plans";

export default function Home() {
  const [openFaq, setOpenFaq] = useState<number | null>(null);

  const faqs = [
    {
      q: "什么是 Coding Plan？",
      a: "Coding Plan 是国内各大 AI 平台推出的编程专属订阅套餐。订阅后，你可以在 Claude Code、Cursor、Cline 等主流编程工具中使用国产大模型进行 AI 辅助编程，无需按 token 付费，按月/季/年订阅即可畅用。",
    },
    {
      q: "哪个平台性价比最高？",
      a: "入门体验推荐 MiniMax Starter（¥29/月）或火山引擎/阿里云的首月特惠（低至 ¥7.9）。看重模型多样性选火山引擎方舟，看重 MCP 工具选智谱 GLM（含在套餐内，有月度额度限制）。",
    },
    {
      q: "可以退款吗？",
      a: "各平台政策不同。大部分平台不支持订阅后退款，建议先从低档套餐开始体验。",
    },
  ];

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      {/* Hero Section */}
      <header className="relative overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-b from-blue-600/20 to-transparent"></div>
        <div className="relative max-w-6xl mx-auto px-4 py-16 sm:py-20">
          <div className="text-center mb-12">
            <h1 className="text-4xl sm:text-5xl font-bold mb-4 bg-gradient-to-r from-blue-400 to-purple-400 bg-clip-text text-transparent">
              AI Coding Plan 对比
            </h1>
            <p className="text-lg text-gray-400 max-w-2xl mx-auto">
              对比智谱 GLM、MiniMax、Kimi、火山引擎、阿里云百炼、腾讯云、小米 MiMo 等国内主流 AI 编程套餐
            </p>
          </div>
          <div className="flex flex-wrap justify-center gap-8 text-center">
            <div className="hero-stat">
              <div className="text-4xl font-bold text-white">7</div>
              <div className="text-sm text-gray-400 mt-1">平台对比</div>
            </div>
            <div className="hero-stat">
              <div className="text-4xl font-bold text-green-400">¥7.9</div>
              <div className="text-sm text-gray-400 mt-1">最低首月价</div>
            </div>
            <div className="hero-stat">
              <div className="text-4xl font-bold text-blue-400">10+</div>
              <div className="text-sm text-gray-400 mt-1">可选模型</div>
            </div>
            <div className="hero-stat">
              <div className="text-4xl font-bold text-purple-400">30+</div>
              <div className="text-sm text-gray-400 mt-1">套餐档位</div>
            </div>
          </div>
        </div>
      </header>

      {/* Navigation Bar */}
      <nav className="sticky top-0 z-50 bg-[#111]/95 backdrop-blur-sm border-b border-gray-800">
        <div className="max-w-6xl mx-auto px-4">
          <div className="flex items-center gap-1 overflow-x-auto py-3 scrollbar-hide">
            {platforms.map((platform) => (
              <a
                key={platform.id}
                href={`#${platform.id}`}
                className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium whitespace-nowrap transition-colors hover:bg-white/10"
                style={{ color: platform.color }}
              >
                <span>{platform.icon}</span>
                <span>{platform.name}</span>
              </a>
            ))}
          </div>
        </div>
      </nav>

      {/* Quick Compare Table */}
      <section className="max-w-6xl mx-auto px-4 py-12">
        <h2 className="text-2xl font-bold text-center mb-8">
          各平台入门套餐一览，找到最适合你的起点
        </h2>
        <div className="overflow-x-auto rounded-xl border border-gray-800">
          <table className="w-full">
            <thead>
              <tr className="border-b border-gray-800">
                <th className="px-4 py-4 text-left text-sm font-medium text-gray-400 bg-[#111]">
                  平台
                </th>
                <th className="px-4 py-4 text-left text-sm font-medium text-gray-400 bg-[#111]">
                  入门价
                </th>
                <th className="px-4 py-4 text-left text-sm font-medium text-gray-400 bg-[#111]">
                  首月特惠
                </th>
                <th className="px-4 py-4 text-left text-sm font-medium text-gray-400 bg-[#111]">
                  套餐数
                </th>
                <th className="px-4 py-4 text-left text-sm font-medium text-gray-400 bg-[#111]">
                  亮点
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800">
              {platforms.map((platform) => (
                <tr
                  key={platform.id}
                  className="hover:bg-white/5 transition-colors"
                >
                  <td className="px-4 py-4">
                    <a
                      href={`#${platform.id}`}
                      className="flex items-center gap-3 group"
                    >
                      <div
                        className="w-10 h-10 rounded-lg flex items-center justify-center text-xl transition-transform group-hover:scale-110"
                        style={{ backgroundColor: platform.color + "20" }}
                      >
                        {platform.icon}
                      </div>
                      <div>
                        <div
                          className="font-medium transition-colors group-hover:underline"
                          style={{ color: platform.color }}
                        >
                          {platform.name}
                        </div>
                        <div className="text-sm text-gray-500">
                          {platform.models.slice(0, 2).join(" / ")}
                        </div>
                      </div>
                    </a>
                  </td>
                  <td className="px-4 py-4">
                    <span
                      className="text-lg font-bold"
                      style={{ color: platform.color }}
                    >
                      ¥{platform.plans[0].price}
                    </span>
                    <span className="text-sm text-gray-500">
                      {platform.plans[0].priceUnit}
                    </span>
                  </td>
                  <td className="px-4 py-4">
                    {platform.plans[0].note?.includes("首月") ||
                    platform.plans[0].note?.includes("送") ? (
                      <span className="inline-flex items-center px-2.5 py-1 rounded-md text-xs font-medium bg-green-500/10 text-green-400 border border-green-500/20">
                        {platform.plans[0].note?.includes("送")
                          ? platform.plans[0].note
                          : platform.plans[0].note?.split("·")[0]}
                      </span>
                    ) : platform.plans[0].badge ? (
                      <span
                        className="inline-flex items-center px-2.5 py-1 rounded-md text-xs font-medium border"
                        style={{
                          backgroundColor: platform.color + "10",
                          borderColor: platform.color + "30",
                          color: platform.color,
                        }}
                      >
                        {platform.plans[0].badge}
                      </span>
                    ) : (
                      <span className="text-gray-600">-</span>
                    )}
                  </td>
                  <td className="px-4 py-4 text-gray-400">
                    {platform.plans.length} 档
                  </td>
                  <td className="px-4 py-4">
                    <span className="text-sm text-gray-400">
                      {platform.highlights[0]}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Platform Cards */}
      <section className="max-w-6xl mx-auto px-4 py-12">
        <h2 className="text-2xl font-bold text-center mb-2">
          深入了解每个平台的套餐配置与定价
        </h2>
        <p className="text-center text-gray-500 mb-12">
          点击平台名称可跳转官网查看详情
        </p>
        <div className="space-y-8">
          {platforms.map((platform) => (
            <div
              key={platform.id}
              id={platform.id}
              className="rounded-xl border border-gray-800 overflow-hidden scroll-mt-20"
              style={{ borderColor: platform.color + "30" }}
            >
              {/* Platform Header */}
              <div
                className="px-6 py-5"
                style={{
                  background: `linear-gradient(135deg, ${platform.color}15 0%, transparent 100%)`,
                }}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-4">
                    <div
                      className="w-12 h-12 rounded-xl flex items-center justify-center text-2xl"
                      style={{ backgroundColor: platform.color + "20" }}
                    >
                      {platform.icon}
                    </div>
                    <div>
                      <h3 className="text-xl font-bold">
                        <a
                          href={platform.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="hover:underline"
                          style={{ color: platform.color }}
                        >
                          {platform.name} Coding Plan
                        </a>
                      </h3>
                      <p className="text-sm text-gray-400 mt-1">
                        {platform.subtitle}
                      </p>
                    </div>
                  </div>
                  <a
                    href={platform.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-sm px-4 py-2 rounded-lg border border-gray-700 text-gray-400 hover:text-white hover:border-gray-500 transition-colors"
                  >
                    访问官网 →
                  </a>
                </div>
              </div>

              {/* Plans Grid */}
              <div className="p-6 bg-[#111]">
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
                  {platform.plans.map((plan, idx) => (
                    <div
                      key={idx}
                      className="relative p-4 rounded-lg border border-gray-800 hover:border-gray-700 transition-colors bg-[#0a0a0a]"
                    >
                      {plan.badge && (
                        <div
                          className="absolute -top-2.5 left-4 px-2 py-0.5 rounded text-xs font-medium"
                          style={{
                            backgroundColor: platform.color,
                            color: "#000",
                          }}
                        >
                          {plan.badge}
                        </div>
                      )}
                      <div className="mb-3">
                        <span className="text-lg font-medium text-gray-300">
                          {plan.name}
                        </span>
                      </div>
                      <div className="mb-2">
                        <span
                          className="text-3xl font-bold"
                          style={{ color: platform.color }}
                        >
                          ¥{plan.price}
                        </span>
                        <span className="text-sm text-gray-500">
                          {plan.priceUnit}
                        </span>
                      </div>
                      {plan.note && (
                        <div className="text-xs text-gray-500 mb-3">
                          {plan.note}
                        </div>
                      )}
                      <div className="space-y-1.5">
                        {plan.features.map((feature, fidx) => (
                          <div
                            key={fidx}
                            className="flex items-center gap-2 text-sm text-gray-400"
                          >
                            <span style={{ color: platform.color }}>✓</span>
                            {feature}
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>

                {/* Models & Highlights */}
                <div className="mt-6 pt-6 border-t border-gray-800">
                  <div className="flex flex-wrap gap-6">
                    <div>
                      <div className="text-xs font-medium text-gray-500 mb-2 uppercase tracking-wider">
                        支持模型
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {platform.models.map((model, idx) => (
                          <span
                            key={idx}
                            className="inline-flex items-center px-2.5 py-1 rounded-md text-xs bg-white/5 text-gray-300 border border-gray-800"
                          >
                            {model}
                          </span>
                        ))}
                      </div>
                    </div>
                    <div>
                      <div className="text-xs font-medium text-gray-500 mb-2 uppercase tracking-wider">
                        平台亮点
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {platform.highlights.map((highlight, idx) => (
                          <span
                            key={idx}
                            className="inline-flex items-center px-2.5 py-1 rounded-md text-xs border"
                            style={{
                              backgroundColor: platform.color + "10",
                              borderColor: platform.color + "20",
                              color: platform.color,
                            }}
                          >
                            {highlight}
                          </span>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* FAQ - Accordion Style */}
      <section className="max-w-3xl mx-auto px-4 py-16">
        <h2 className="text-2xl font-bold text-center mb-8">常见问题</h2>
        <div className="space-y-3">
          {faqs.map((faq, idx) => (
            <div
              key={idx}
              className="rounded-xl border border-gray-800 overflow-hidden"
            >
              <button
                onClick={() => setOpenFaq(openFaq === idx ? null : idx)}
                className="w-full px-6 py-5 flex items-center justify-between text-left hover:bg-white/5 transition-colors"
              >
                <span className="font-medium text-white pr-4">{faq.q}</span>
                <svg
                  className={`w-5 h-5 text-gray-400 flex-shrink-0 transition-transform ${
                    openFaq === idx ? "rotate-180" : ""
                  }`}
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M19 9l-7 7-7-7"
                  />
                </svg>
              </button>
              {openFaq === idx && (
                <div className="px-6 pb-5 text-gray-400 leading-relaxed border-t border-gray-800 pt-4">
                  {faq.a}
                </div>
              )}
            </div>
          ))}
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-gray-800">
        <div className="max-w-6xl mx-auto px-4 py-8">
          <div className="flex flex-col sm:flex-row justify-between items-center gap-4">
            <div className="text-sm text-gray-500">
              数据更新于 2026 年 4 月，价格以各平台官网为准
            </div>
            <div className="flex items-center gap-4 text-sm text-gray-500">
              <span>
                Powered by{" "}
                <a
                  href="https://github.com/anthropics/claude-code"
                  className="text-blue-400 hover:underline"
                >
                  Claude Code
                </a>
              </span>
              <span>·</span>
              <span>
                参考{" "}
                <a
                  href="https://codingplan.org"
                  className="text-blue-400 hover:underline"
                >
                  codingplan.org
                </a>
              </span>
            </div>
          </div>
        </div>
      </footer>
    </div>
  );
}
