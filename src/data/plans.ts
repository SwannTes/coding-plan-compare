export interface Plan {
  name: string;
  price: number;
  priceUnit: string;
  originalPrice?: string;
  note?: string;
  badge?: string;
  badgeColor?: string;
  features: string[];
}

export interface Platform {
  id: string;
  name: string;
  subtitle: string;
  icon: string;
  color: string;
  url: string;
  plans: Plan[];
  highlights: string[];
  models: string[];
}

export const platforms: Platform[] = [
  {
    id: "xiaomi",
    name: "小米 MiMo Token Plan",
    subtitle: "一次购买，畅用 MiMo 全系旗舰模型",
    icon: "📱",
    color: "#FF6700",
    url: "https://platform.xiaomimimo.com/token-plan",
    plans: [
      {
        name: "Lite",
        price: 39,
        priceUnit: "/月",
        note: "60M Credits",
        badge: "入门",
        badgeColor: "rgba(255,107,53,.12)",
        features: ["60M Credits/月", "适合轻度使用", "约120轮中等任务"],
      },
      {
        name: "Standard",
        price: 99,
        priceUnit: "/月",
        note: "200M Credits",
        badge: "推荐",
        badgeColor: "rgba(239,68,68,.15)",
        features: ["200M Credits/月", "适合日常使用", "约400轮中等任务"],
      },
      {
        name: "Pro",
        price: 329,
        priceUnit: "/月",
        note: "700M Credits",
        badge: "专业",
        badgeColor: "rgba(168,85,247,.15)",
        features: ["700M Credits/月", "适合重度使用", "约1400轮中等任务"],
      },
      {
        name: "Max",
        price: 659,
        priceUnit: "/月",
        note: "1600M Credits",
        badge: "旗舰",
        badgeColor: "rgba(236,72,153,.15)",
        features: ["1600M Credits/月", "适合企业级使用", "约3200轮中等任务"],
      },
    ],
    highlights: [
      "支持 MiMo-V2.5-Pro/Omni 等 8 款模型",
      "TTS 模型限时免费",
      "夜间 (0-8 点) 消耗 0.8 倍",
      "首购 88 折优惠",
    ],
    models: ["MiMo-V2.5-Pro", "MiMo-V2.5", "MiMo-V2-Pro", "MiMo-V2-Omni"],
  },
  {
    id: "zhipu",
    name: "智谱 GLM Coding Plan",
    subtitle: "GLM-5/4.7/4.6 全系列，含 MCP 工具",
    icon: "🧠",
    color: "#4F6EF7",
    url: "https://open.bigmodel.cn/pricing",
    plans: [
      {
        name: "Lite",
        price: 49,
        priceUnit: "/月",
        note: "包季 ¥44/月",
        badge: "轻量级",
        badgeColor: "rgba(79,110,247,.15)",
        features: ["GLM-5/4.7/4.6", "含 MCP 工具", "适合轻度使用"],
      },
      {
        name: "Pro",
        price: 149,
        priceUnit: "/月",
        note: "包季 ¥134/月",
        badge: "最受欢迎",
        badgeColor: "rgba(239,68,68,.15)",
        features: ["GLM-5/4.7/4.6", "含 MCP 工具", "更高用量限制"],
      },
      {
        name: "Max",
        price: 469,
        priceUnit: "/月",
        note: "包季 ¥422/月",
        badge: "量大管饱",
        badgeColor: "rgba(168,85,247,.15)",
        features: ["GLM-5/4.7/4.6", "含 MCP 工具", "超高用量限制"],
      },
    ],
    highlights: ["GLM-5 在 SWE-bench 表现优异", "套餐含 MCP 工具额度", "包季更优惠"],
    models: ["GLM-5", "GLM-4.7", "GLM-4.6"],
  },
  {
    id: "minimax",
    name: "MiniMax Coding Plan",
    subtitle: "100+ TPS，6 档套餐任选",
    icon: "⚡",
    color: "#00D4AA",
    url: "https://www.minimaxi.com/pricing",
    plans: [
      {
        name: "Starter",
        price: 29,
        priceUnit: "/月",
        badge: "入门",
        badgeColor: "rgba(255,107,53,.12)",
        features: ["MiniMax-M2.7", "入门级用量", "适合体验"],
      },
      {
        name: "Plus",
        price: 49,
        priceUnit: "/月",
        features: ["MiniMax-M2.7", "更高用量", "适合日常"],
      },
      {
        name: "Max",
        price: 119,
        priceUnit: "/月",
        features: ["MiniMax-M2.7", "大量使用", "适合重度"],
      },
      {
        name: "Plus 极速版",
        price: 98,
        priceUnit: "/月",
        features: ["MiniMax-M2.7", "100+ TPS", "极速响应"],
      },
      {
        name: "Max 极速版",
        price: 199,
        priceUnit: "/月",
        badge: "超值之选",
        badgeColor: "rgba(239,68,68,.15)",
        features: ["MiniMax-M2.7", "100+ TPS", "超高用量"],
      },
      {
        name: "Ultra 极速版",
        price: 399,
        priceUnit: "/月",
        badge: "极速畅用",
        badgeColor: "rgba(168,85,247,.15)",
        features: ["MiniMax-M2.7", "100+ TPS", "不限量"],
      },
    ],
    highlights: ["最低 ¥29/月起", "极速版 100+ TPS", "6 档套餐灵活选择"],
    models: ["MiniMax-M2.7"],
  },
  {
    id: "kimi",
    name: "Kimi Coding Plan",
    subtitle: "全面接入 K2.5，年付最高立省 ¥240",
    icon: "🌙",
    color: "#8B5CF6",
    url: "https://platform.moonshot.cn/pricing",
    plans: [
      {
        name: "Andante",
        price: 49,
        priceUnit: "/月",
        note: "年付 ¥39/月（¥468/年）",
        badge: "基础",
        badgeColor: "rgba(139,92,246,.12)",
        features: ["Kimi K2.5", "基础用量", "年付更优惠"],
      },
      {
        name: "Moderato",
        price: 99,
        priceUnit: "/月",
        note: "年付 ¥79/月（¥948/年）",
        badge: "推荐",
        badgeColor: "rgba(239,68,68,.15)",
        features: ["Kimi K2.5", "更高用量", "年付更优惠"],
      },
    ],
    highlights: ["全面接入 K2.5", "限时额度扩容 3 倍", "年付最高立省 ¥240"],
    models: ["Kimi K2.5"],
  },
  {
    id: "volcengine",
    name: "火山引擎方舟 Coding Plan",
    subtitle: "6 款模型，最多选择",
    icon: "🌋",
    color: "#FF4D4F",
    url: "https://www.volcengine.com/product/ark",
    plans: [
      {
        name: "Lite",
        price: 40,
        priceUnit: "/月",
        note: "首月 ¥8.91（2.5 折）",
        badge: "首月特惠",
        badgeColor: "rgba(255,77,79,.15)",
        features: ["6 款模型可选", "基础用量", "首月超低价"],
      },
      {
        name: "Pro",
        price: 200,
        priceUnit: "/月",
        features: ["6 款模型可选", "更高用量", "适合专业用户"],
      },
    ],
    highlights: ["首月 ¥8.91 超低价", "6 款模型最多选择", "DeepSeek/Qwen/GLM 等"],
    models: ["DeepSeek-V3.2", "Qwen3.5", "GLM-4.7", "Kimi K2.5", "Doubao-Seed"],
  },
  {
    id: "aliyun",
    name: "阿里云百炼 Coding Plan",
    subtitle: "千问系列 + 第三方模型，首月特惠",
    icon: "☁️",
    color: "#FF6A00",
    url: "https://bailian.console.aliyun.com/",
    plans: [
      {
        name: "Lite",
        price: 40,
        priceUnit: "/月",
        note: "首月 ¥7.9 · 续费次月 ¥20",
        badge: "首月 ¥7.9",
        badgeColor: "rgba(255,106,0,.15)",
        features: ["18,000 次/月", "千问系列", "首月超低价"],
      },
      {
        name: "Pro",
        price: 200,
        priceUnit: "/月",
        note: "首月 ¥39.9 · 续费次月 ¥100",
        badge: "专业首选",
        badgeColor: "rgba(239,68,68,.15)",
        features: ["90,000 次/月", "千问系列", "首月特惠"],
      },
    ],
    highlights: ["首月 ¥7.9 最低价", "续费次月半价", "千问系列模型"],
    models: ["Qwen3.5", "Qwen-Coder", "第三方模型"],
  },
  {
    id: "tencentcloud",
    name: "腾讯云 Coding Plan",
    subtitle: "混元自研 + 顶尖第三方模型",
    icon: "🐧",
    color: "#0072FF",
    url: "https://cloud.tencent.com/product/hunyuan",
    plans: [
      {
        name: "Lite",
        price: 40,
        priceUnit: "/月",
        note: "首月 ¥7.9 · 次月 ¥20（5 折）",
        badge: "限时特惠",
        badgeColor: "rgba(0,114,255,.15)",
        features: ["混元自研模型", "首月 ¥7.9", "第三月起原价"],
      },
      {
        name: "Pro",
        price: 200,
        priceUnit: "/月",
        note: "首月 ¥39.9 · 次月 ¥100（5 折）",
        badge: "专业首选",
        badgeColor: "rgba(239,68,68,.15)",
        features: ["混元自研模型", "首月 ¥39.9", "第三月起原价"],
      },
    ],
    highlights: ["首月 ¥7.9", "次月 5 折", "混元自研模型"],
    models: ["混元自研", "第三方模型"],
  },
  {
    id: "baishan",
    name: "白云智算 AI API",
    subtitle: "注册送 ¥450 代金券，按量计费",
    icon: "☁️",
    color: "#06B6D4",
    url: "https://ai.baishan.com",
    plans: [
      {
        name: "按量计费",
        price: 0,
        priceUnit: "",
        note: "注册送 ¥450 代金券",
        badge: "送 ¥450",
        badgeColor: "rgba(6,182,212,.15)",
        features: ["按量计费", "注册送 ¥150", "首次调用再送 ¥300"],
      },
    ],
    highlights: ["注册实名送 ¥150", "首次 API 调用再送 ¥300", "合计最高 ¥450"],
    models: ["多种模型可选"],
  },
];
