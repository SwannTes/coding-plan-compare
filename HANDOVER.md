# coding-plan-compare 项目交接文档

> **给接手者**：读完本文档应能独立完成日常维护（更新价格/模型、加平台）和新增功能。
> **写于**：2026-08-11，最后一次代码核对同步完成。

---

## 0. 一句话定位

**国内 8 大 AI 编程订阅套餐（Coding Plan）的横评对比站**，静态站点，部署在 Vercel + 自定义域 `swann.com.cn`。

---

## 1. 项目元信息（速查表）

| 项 | 值 |
|---|---|
| 仓库 | https://github.com/SwannTes/coding-plan-compare |
| 线上地址 | https://swann.com.cn |
| 本地路径 | `/Users/swann/.claude/xiaopangxia/projects/coding-plan-compare` |
| 技术栈 | Next.js 15.4.11（静态导出）+ React 19 + TypeScript 5.8 + Tailwind CSS 4 |
| 包管理 | npm（`package-lock.json` 已提交） |
| Node 版本 | 20（CI 固定） |
| Vercel Team | `team_4Xw12qEIQVOIFLDnpPJzGIIK` |
| Vercel Project | `prj_0s20WKMso051WD0qfRCZCPQff6bF` |
| 部署方式 | GitHub Actions → curl 调 Vercel API（非官方 action） |
| 触发条件 | push 到 `main` 分支 |

> ⚠️ 接手者注意：项目**没有 README.md**，所有文档入口就是本文档。Vercel 项目的实际配置（域名、环境变量等）需要在 Vercel 后台查看，不在仓库内。

---

## 2. 需求与设计动机

### 2.1 为什么要做

2026 年国内 AI 编程订阅套餐爆发：**至少 8 家厂商**（智谱 GLM、MiniMax、Kimi、火山引擎、阿里云百炼、腾讯云、小米 MiMo、讯飞星辰）推出 Coding Plan，每个平台 2-4 档套餐，模型、价格、用量限制、首月特惠各异。开发者**选择困难**，需要中立横评。

### 2.2 目标用户

- 想用 Claude Code / Cursor / Cline 等 AI 编程工具的国内开发者
- 想从按 token 计费切换到包月套餐的用户
- 比较各平台性价比的决策者

### 2.3 核心交付价值

1. **一眼速览**：首页 hero 区 + 速览表，5 秒内看清入门价
2. **横向对比**：8 平台同页呈现，套餐档数、首月特惠、支持模型一目了然
3. **直达官网**：每个平台都有跳转链接（多数带推广码 `?ic=` / `?source=` / `?code=` 等）
4. **FAQ 答疑**：覆盖"是什么/哪个好/能退款吗"三个高频问题

### 2.4 非目标（明确不做）

- ❌ **不做用户系统**：无登录、无订阅、无支付（纯展示）
- ❌ **不做后端 API**：所有数据硬编码在 `src/data/plans.ts`
- ❌ **不做实时价格抓取**：定期手动更新数据
- ❌ **不做 CMS**：改内容=改代码+commit
- ❌ **不做 i18n**：纯中文站（`html lang="zh-CN"`）

---

## 3. 架构与代码结构

### 3.1 目录树（极简）

```
coding-plan-compare/
├── .github/workflows/
│   └── deploy.yml                  # GitHub Actions 部署脚本
├── src/
│   ├── app/
│   │   ├── layout.tsx              # 根布局 + SEO metadata
│   │   ├── page.tsx                # 唯一页面（Hero + 导航 + 速览表 + 平台卡 + FAQ + Footer）
│   │   └── globals.css             # Tailwind v4 入口 + 暗色主题变量
│   └── data/
│       └── plans.ts                # ★全部业务数据（8 平台 + 24 套餐）
├── next.config.ts                  # 只配了 output: 'export'（静态导出）
├── package.json
├── tailwind.config.* / postcss.config.mjs
└── tsconfig.json
```

**总计 5 个业务文件**——这就是项目的全部。

### 3.2 数据模型（`src/data/plans.ts`）

```typescript
interface Plan {
  name: string;             // 套餐名："Lite" / "Pro" / "Starter" / "无忧版" 等
  price: number;            // 月价（数字，单位元）
  priceUnit: string;        // "/月" 等
  originalPrice?: string;    // 原价（划线价，未使用）
  note?: string;             // 备注："包季 ¥44.1/月（9折）"、"首月 ¥7.9" 等
  badge?: string;           // 角标："最受欢迎" / "推荐" / "限时特惠"
  badgeColor?: string;      // 角标背景色（rgba）
  features: string[];       // 套餐特性（✓ 列表显示）
}

interface Platform {
  id: string;               // 锚点 id，如 "zhipu"
  name: string;             // 显示名："智谱 GLM"
  subtitle: string;         // 一句话卖点
  icon: string;             // emoji 图标
  color: string;            // 主题色（hex）
  url: string;              // 官网跳转链接（带推广参数）
  plans: Plan[];            // 2-4 档套餐
  highlights: string[];     // 平台亮点 chips
  models: string[];         // 支持的模型列表
}
```

**关键设计**：
- 所有平台数据放在一个 `platforms: Platform[]` 数组
- 页面所有区块（导航、速览表、平台卡片）都从这一份数据**派生**
- 新增平台 = 在数组里加一个对象，不用改页面代码

### 3.3 当前 8 个平台

| id | 名称 | 套餐档数 | 入门价 | 关键特点 |
|---|---|---|---|---|
| `zhipu` | 智谱 GLM | 3 | ¥49 | 含 MCP 工具 |
| `minimax` | MiniMax | 4 | ¥29 | 最低入门价，多模态 |
| `kimi` | Kimi | 4 | ¥39 | K2.7 Code，年付省最多 |
| `volcengine` | 火山引擎方舟 | 2 | ¥40 | 多模型自由切换 |
| `aliyun` | 阿里云百炼 | 2 | ¥40 | 首月 ¥7.9，Qwen 系列 |
| `tencentcloud` | 腾讯云 | 2 | ¥40 | 首月 ¥7.9，混元自研 |
| `xiaomi` | 小米 MiMo | 4 | ¥39 | Credits 额度，夜间 0.8 倍 |
| `xfyun` | 讯飞星辰 | 3 | ¥19 | 不限次数，双协议接口 |

### 3.4 页面区块结构（`src/app/page.tsx`）

页面是单文件单组件，从上到下：

1. **Hero**：标题 + 4 个统计数字（7 平台 / ¥7.9 / 10+ 模型 / 30+ 套餐）
   - ⚠️ hero 里写"7 平台"——**已过时**，实际是 8 平台，改 `plans.ts` 时记得同步改这里
2. **Sticky 导航条**：锚点跳转到各平台卡片
3. **速览表**：5 列（平台 / 入门价 / 首月特惠 / 套餐数 / 亮点）
4. **平台卡片**：每平台一张大卡片，含套餐网格 + 模型列表 + 亮点 chips
5. **FAQ 折叠**：3 个问题，用 `useState` 控制开合
6. **Footer**：版权 + Powered by Claude Code + 参考 codingplan.org

### 3.5 关键设计决策（why）

| 决策 | 原因 |
|---|---|
| **静态导出**（`output: 'export'`） | 内容不常变动 → 不需要 SSR；可以部署到任何静态服务；Vercel 免费额度够用 |
| **数据硬编码**（无 CMS） | 维护频次低（每月 1-2 次），加 CMS 反而增加复杂度 |
| **单页面单组件**（不打散） | 总代码 < 500 行，拆组件收益小 |
| **Tailwind v4** | v4 配置更简单（CSS-first），无 `tailwind.config.js` |
| **emoji 作图标** | 不依赖图标库，省体积；中文站够用 |
| **暗色为主** | 科技感；`prefers-color-scheme` 自动切换 |

---

## 4. 部署流程

### 4.1 全链路

```
开发者 push main
    ↓
GitHub Actions 触发（.github/workflows/deploy.yml）
    ↓
ubuntu-latest runner
    ↓
① checkout@v4
② setup-node@v4（Node 20）
③ npm ci（基于 lockfile 严格安装）
④ npm run build（next build，输出到 ./out/）
⑤ curl POST https://api.vercel.com/v1/deployments
   带 Bearer $VERCEL_TOKEN + projectId + teamId
    ↓
Vercel 接管：拉取构建产物 → 分配域名 → 自动更新 swann.com.cn
```

### 4.2 必需的 GitHub Secret

| Secret 名 | 用途 | 在哪获取 |
|---|---|---|
| `VERCEL_TOKEN` | Vercel API 访问令牌 | https://vercel.com/account/tokens |

**只需要一个 secret**——因为不用官方 GitHub Action，所有 Vercel 配置（teamId、projectId）已硬编码在 workflow 文件里。

### 4.3 部署踩坑（必读）

- **commit `caf184f8`**：最初用 `vercel-action`，后来**改为 curl 直调 API**。原因未在 commit message 说明，怀疑是 action 版本兼容或权限问题。
- **Node 版本固定为 20**：不能用 18 或 22，会报 peer deps 警告（实测可跑但官方推荐 20）。

### 4.4 本地调试

```bash
cd /Users/swann/.claude/xiaopangxia/projects/coding-plan-compare
npm ci            # 首次或 lockfile 变了才需要
npm run dev       # 开发模式，http://localhost:3000
npm run build     # 构建到 ./out/
npm run lint      # ESLint 检查
```

---

## 5. 维护工作（最重要的一节）

### 5.1 日常维护任务

#### A. 更新平台价格/模型（最频繁，每月 1-2 次）

**触发**：某平台发新版套餐、新模型上线、价格调整

**操作**：
1. 打开 `src/data/plans.ts`
2. 找到对应平台的 `plans[]` 或 `models[]` 数组
3. 直接改字段值
4. 同步更新 `src/app/page.tsx` 的 hero 统计数字（如果平台数或套餐总数变了）
5. 同步更新 `src/app/page.tsx` 的 Footer 时间戳（"数据更新于 XXXX 年 X 月"）
6. commit + push → 自动部署

**典型 commit 模板**：`feat: update models - Kimi K2.6 and GLM-5.1`

#### B. 新增平台（偶尔，一年 1-3 次）

**触发**：新厂商进入市场

**操作**：
1. 在 `src/data/plans.ts` 的 `platforms[]` 数组**末尾**追加新对象
2. 字段填写参考已有平台（特别是 `id` 用拼音英文小写）
3. 给页面 hero 的"平台对比"数字 +1
4. 同步修 Footer 时间戳
5. 检查 FAQ 的"哪个性价比高"答案里要不要提一句
6. commit + push

#### C. 移除平台（极少）

**触发**：平台倒闭或停止 Coding Plan

**操作**：
1. 从 `plans.ts` 删除该平台对象
2. 同步修 hero 数字 + Footer 时间戳
3. 检查 FAQ 答案里有没有引用

### 5.2 已知技术债 / 待办

| 项 | 严重度 | 建议处理 |
|---|---|---|
| `page.tsx` 第 40 行 hero 写"7"平台 | ~~高~~ | ✅ **2026-08-12 已修** |
| `page.tsx` 第 388 行 Footer "数据更新于 2026 年 4 月" | ~~高~~ | ✅ **2026-08-12 已修** |
| 没有 README.md | 中 | 建议加一份精简版（链接到本 HANDOVER） |
| 没有自动化测试 | 低 | 当前体量不需要；如果加新功能建议加 |
| 没有数据更新提醒机制 | 中 | 可加 GitHub Issue 模板或定时提醒 |
| 没有 SEO sitemap/robots | 中 | 可加 `app/sitemap.ts` + `app/robots.ts` |
| 没有 analytics | 低 | 可加 Vercel Analytics（零成本） |
| 暗色为主，亮色样式未优化 | 低 | 当前数据表明用户偏好暗色（科技产品） |
| `/shenzhenshekang/` 下社康脚本公网可下载 | 中 | 已知风险（2026-09 阿望确认暂缓处理，单人使用场景）：脚本含社康业务逻辑，无凭证/内网地址暴露。未来方向：会员制鉴权下载（serverless 验 token，客户端 button_service.py 下载加 header） |

### 5.3 扩展方向（如果以后要做）

- **数据可视化**：加图表对比各平台性价比（每元能买多少 token）
- **订阅追踪**：让用户标记自己订阅了哪些平台（需要 localStorage 或后端）
- **价格变动历史**：每次更新 plans.ts 时自动 commit 历史
- **新平台接入模板**：写个脚本从标准化输入生成 plans.ts 条目
- **RSS/Newsletter**：价格变动时推送订阅用户

---

## 6. 接手者 Checklist

第一次接手这个项目，按顺序做这些：

- [ ] 克隆仓库到本地
- [ ] `npm ci` 装依赖
- [ ] `npm run dev` 跑起来，确认 http://localhost:3000 正常
- [ ] 对照本文档第 3.3 节看 `plans.ts`，确认 8 个平台都认识
- [ ] 对照本文档第 4 节确认你能独立部署（先 dry run 改个字符串试试）
- [ ] **修复第 5.2 节里列的 2 个高严重度技术债**（hero 数字 + Footer 时间戳）
- [ ] 订阅平台官方公告渠道（公众号 / 文档站），方便及时更新价格
- [ ] 把本文档的 GitHub 链接加到浏览器书签

---

## 7. 联系与上下文

- **项目作者**：望望（OpenClaw 团队，自由职业者）
- **作者偏好**：严谨专业，第一性原理，禁止猜测
- **相关记忆文件**：`/Users/swann/.claude/projects/-Users-swann/memory/project_coding_plan_compare.md`（含仓库/部署/技术栈元信息快照）
- **项目目录规则**：所有项目文件统一放 `~/.claude/xiaopangxia/projects/`

---

## 8. 变更日志（本文档）

- 2026-08-11：首次创建。从代码 + git log + 实际部署配置倒推还原，原始的 `~/.openclaw/workspace/main/交接_coding_plan_部署.md` 已丢失。
- 2026-08-12：顺手修复 2 个高严重度技术债——`page.tsx` 第 40 行 hero 数字 7 → 8；第 388 行 Footer 时间戳改为"2026 年 8 月"。