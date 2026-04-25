"use client";

import { platforms } from "@/data/plans";

export default function Home() {
  return (
    <div className="min-h-screen bg-gradient-to-b from-gray-50 to-white dark:from-gray-900 dark:to-gray-800">
      {/* Hero Section */}
      <header className="relative overflow-hidden bg-gradient-to-r from-blue-600 to-purple-600 text-white">
        <div className="absolute inset-0 bg-[url('/grid.svg')] opacity-10"></div>
        <div className="relative max-w-7xl mx-auto px-4 py-16 sm:py-24">
          <h1 className="text-4xl sm:text-5xl font-bold text-center mb-4">
            AI Coding Plan 对比
          </h1>
          <p className="text-xl text-center text-blue-100 mb-8">
            国内 8 大平台套餐横评，找到最适合你的 AI 编程方案
          </p>
          <div className="flex flex-wrap justify-center gap-6 text-center">
            <div className="bg-white/10 rounded-lg px-6 py-4">
              <div className="text-3xl font-bold">8</div>
              <div className="text-sm text-blue-100">平台对比</div>
            </div>
            <div className="bg-white/10 rounded-lg px-6 py-4">
              <div className="text-3xl font-bold">¥7.9</div>
              <div className="text-sm text-blue-100">最低首月价</div>
            </div>
            <div className="bg-white/10 rounded-lg px-6 py-4">
              <div className="text-3xl font-bold">10+</div>
              <div className="text-sm text-blue-100">可选模型</div>
            </div>
            <div className="bg-white/10 rounded-lg px-6 py-4">
              <div className="text-3xl font-bold">30+</div>
              <div className="text-sm text-blue-100">套餐档位</div>
            </div>
          </div>
        </div>
      </header>

      {/* Quick Compare Table */}
      <section className="max-w-7xl mx-auto px-4 py-12">
        <h2 className="text-2xl font-bold text-center mb-8 text-gray-800 dark:text-white">
          快速对比
        </h2>
        <div className="overflow-x-auto">
          <table className="w-full bg-white dark:bg-gray-800 rounded-xl shadow-lg overflow-hidden">
            <thead className="bg-gray-50 dark:bg-gray-700">
              <tr>
                <th className="px-4 py-3 text-left text-sm font-medium text-gray-500 dark:text-gray-300">
                  平台
                </th>
                <th className="px-4 py-3 text-left text-sm font-medium text-gray-500 dark:text-gray-300">
                  入门价
                </th>
                <th className="px-4 py-3 text-left text-sm font-medium text-gray-500 dark:text-gray-300">
                  首月特惠
                </th>
                <th className="px-4 py-3 text-left text-sm font-medium text-gray-500 dark:text-gray-300">
                  套餐数
                </th>
                <th className="px-4 py-3 text-left text-sm font-medium text-gray-500 dark:text-gray-300">
                  亮点
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 dark:divide-gray-600">
              {platforms.map((platform) => (
                <tr
                  key={platform.id}
                  className="hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
                >
                  <td className="px-4 py-4">
                    <div className="flex items-center gap-2">
                      <span className="text-2xl">{platform.icon}</span>
                      <div>
                        <div className="font-medium text-gray-900 dark:text-white">
                          {platform.name}
                        </div>
                        <div className="text-sm text-gray-500 dark:text-gray-400">
                          {platform.models.slice(0, 2).join(", ")}
                        </div>
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-4">
                    <span className="text-lg font-bold text-blue-600 dark:text-blue-400">
                      ¥{platform.plans[0].price}/{platform.plans[0].priceUnit.replace("/", "")}
                    </span>
                  </td>
                  <td className="px-4 py-4">
                    {platform.plans[0].note?.includes("首月") ? (
                      <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">
                        {platform.plans[0].note.split("·")[0]}
                      </span>
                    ) : (
                      <span className="text-gray-400">-</span>
                    )}
                  </td>
                  <td className="px-4 py-4 text-gray-600 dark:text-gray-300">
                    {platform.plans.length} 档
                  </td>
                  <td className="px-4 py-4">
                    <span className="text-sm text-gray-600 dark:text-gray-300">
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
      <section className="max-w-7xl mx-auto px-4 py-12">
        <h2 className="text-2xl font-bold text-center mb-8 text-gray-800 dark:text-white">
          详细套餐
        </h2>
        <div className="grid gap-8 md:grid-cols-2">
          {platforms.map((platform) => (
            <div
              key={platform.id}
              className="bg-white dark:bg-gray-800 rounded-xl shadow-lg overflow-hidden border border-gray-100 dark:border-gray-700"
              style={{ borderColor: platform.color + "40" }}
            >
              <div
                className="px-6 py-4"
                style={{ backgroundColor: platform.color + "10" }}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <span className="text-3xl">{platform.icon}</span>
                    <div>
                      <h3 className="text-lg font-bold text-gray-900 dark:text-white">
                        {platform.name}
                      </h3>
                      <p className="text-sm text-gray-600 dark:text-gray-300">
                        {platform.subtitle}
                      </p>
                    </div>
                  </div>
                  <a
                    href={platform.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-sm text-blue-600 hover:text-blue-800 dark:text-blue-400"
                  >
                    官网 →
                  </a>
                </div>
              </div>
              <div className="p-6">
                <div className="space-y-4">
                  {platform.plans.map((plan, idx) => (
                    <div
                      key={idx}
                      className="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-700 rounded-lg"
                    >
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-gray-900 dark:text-white">
                            {plan.name}
                          </span>
                          {plan.badge && (
                            <span
                              className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium"
                              style={{
                                backgroundColor: plan.badgeColor || "rgba(0,0,0,.1)",
                                color: platform.color,
                              }}
                            >
                              {plan.badge}
                            </span>
                          )}
                        </div>
                        {plan.note && (
                          <div className="text-sm text-gray-500 dark:text-gray-400">
                            {plan.note}
                          </div>
                        )}
                      </div>
                      <div className="text-right">
                        <div className="text-xl font-bold" style={{ color: platform.color }}>
                          ¥{plan.price}
                          <span className="text-sm font-normal text-gray-500">
                            {plan.priceUnit}
                          </span>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
                <div className="mt-4 pt-4 border-t border-gray-200 dark:border-gray-600">
                  <div className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                    支持模型：
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {platform.models.map((model, idx) => (
                      <span
                        key={idx}
                        className="inline-flex items-center px-2 py-0.5 rounded text-xs bg-gray-100 dark:bg-gray-600 text-gray-600 dark:text-gray-300"
                      >
                        {model}
                      </span>
                    ))}
                  </div>
                </div>
                <div className="mt-3">
                  {platform.highlights.map((highlight, idx) => (
                    <div
                      key={idx}
                      className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300 mt-1"
                    >
                      <span style={{ color: platform.color }}>✓</span>
                      {highlight}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* FAQ */}
      <section className="max-w-3xl mx-auto px-4 py-12">
        <h2 className="text-2xl font-bold text-center mb-8 text-gray-800 dark:text-white">
          常见问题
        </h2>
        <div className="space-y-4">
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow p-6">
            <h3 className="font-medium text-gray-900 dark:text-white mb-2">
              什么是 Coding Plan？
            </h3>
            <p className="text-gray-600 dark:text-gray-300">
              Coding Plan 是国内各大 AI 平台推出的编程专属订阅套餐。订阅后，你可以在 Claude Code、Cursor、Cline
              等主流编程工具中使用国产大模型进行 AI 辅助编程，无需按 token 付费，按月/季/年订阅即可畅用。
            </p>
          </div>
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow p-6">
            <h3 className="font-medium text-gray-900 dark:text-white mb-2">
              哪个平台性价比最高？
            </h3>
            <p className="text-gray-600 dark:text-gray-300">
              入门体验推荐 MiniMax Starter（¥29/月）或火山引擎/阿里云的首月特惠（低至
              ¥7.9）。看重模型多样性选火山引擎方舟，看重 MCP 工具选智谱 GLM（含在套餐内）。
            </p>
          </div>
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow p-6">
            <h3 className="font-medium text-gray-900 dark:text-white mb-2">
              可以退款吗？
            </h3>
            <p className="text-gray-600 dark:text-gray-300">
              各平台政策不同。大部分平台不支持订阅后退款，建议先从低档套餐开始体验。
            </p>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="bg-gray-50 dark:bg-gray-900 border-t border-gray-200 dark:border-gray-700">
        <div className="max-w-7xl mx-auto px-4 py-8 text-center text-gray-500 dark:text-gray-400 text-sm">
          <p>数据更新于 2025 年 4 月，价格以各平台官网为准</p>
          <p className="mt-2">
            由{" "}
            <a
              href="https://github.com/anthropics/claude-code"
              className="text-blue-600 hover:underline"
            >
              Claude Code
            </a>{" "}
            驱动
          </p>
        </div>
      </footer>
    </div>
  );
}
