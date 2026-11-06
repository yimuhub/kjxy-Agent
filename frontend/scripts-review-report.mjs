/**
 * 五维度综合审查报告生成器（v3.70）：
 * 读 docs/review-shots/*.jpg 转 base64 内嵌，产出自包含单文件
 * docs/review-report-2026-11-07.html。用法：node scripts-review-report.mjs
 */
import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const shotDir = resolve(here, '../docs/review-shots')
const outPath = resolve(here, '../docs/review-report-2026-11-07.html')

const img = name => {
  const b64 = readFileSync(resolve(shotDir, `${name}.jpg`)).toString('base64')
  return `data:image/jpeg;base64,${b64}`
}

/* ---------- 五维评分（满分 10） ---------- */
const SCORES = [
  { key: 'eng', label: '代码工程', score: 8.6 },
  { key: 'pm', label: '产品完整度', score: 8.8 },
  { key: 'power', label: '重度用户体验', score: 8.4 },
  { key: 'novice', label: '小白友好度', score: 8.0 },
  { key: 'ops', label: '商业运营潜力', score: 8.2 },
]
const overall = (SCORES.reduce((s, x) => s + x.score, 0) / SCORES.length).toFixed(1)

/* 五边形雷达图坐标：中心 (200,190)，半径 140，顶点朝上 */
const cx = 200
const cy = 196
const R = 140
const pt = (i, ratio) => {
  const angle = -Math.PI / 2 + (i * 2 * Math.PI) / 5
  return `${(cx + R * ratio * Math.cos(angle)).toFixed(1)},${(cy + R * ratio * Math.sin(angle)).toFixed(1)}`
}
const rings = [0.25, 0.5, 0.75, 1]
  .map(r => `<polygon points="${[0, 1, 2, 3, 4].map(i => pt(i, r)).join(' ')}" fill="none" stroke="rgba(140,160,200,.18)" stroke-width="1"/>`)
  .join('')
const axes = [0, 1, 2, 3, 4]
  .map(i => `<line x1="${cx}" y1="${cy}" x2="${pt(i, 1).split(',')[0]}" y2="${pt(i, 1).split(',')[1]}" stroke="rgba(140,160,200,.18)" stroke-width="1"/>`)
  .join('')
const dataPoly = `<polygon points="${SCORES.map((s, i) => pt(i, s.score / 10)).join(' ')}" fill="rgba(99,102,241,.28)" stroke="#818cf8" stroke-width="2"/>`
const dataDots = SCORES.map((s, i) => {
  const [x, y] = pt(i, s.score / 10).split(',')
  return `<circle cx="${x}" cy="${y}" r="4" fill="#818cf8"/>`
}).join('')
const labels = SCORES.map((s, i) => {
  const [x, y] = pt(i, 1.18).split(',')
  return `<text x="${x}" y="${y}" text-anchor="middle" fill="#aab4d4" font-size="13">${s.label} ${s.score}</text>`
}).join('')

const html = `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>kjxy-Agent 五维度综合审查报告 · 2026-11-07</title>
<style>
  :root {
    --bg: #0d1117; --panel: #151b27; --panel2: #1a2130; --border: rgba(150,172,196,.16);
    --text: #e6ecf7; --muted: #93a0b8; --accent: #818cf8; --good: #34d399; --warn: #fbbf24; --bad: #f87171;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text);
    font-family: -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif; line-height: 1.75; }
  .wrap { max-width: 1080px; margin: 0 auto; padding: 32px 20px 80px; }
  h1 { font-size: 26px; margin: 8px 0 4px; }
  h2 { font-size: 21px; margin: 44px 0 6px; padding-top: 18px; border-top: 1px solid var(--border); }
  h3 { font-size: 16px; margin: 22px 0 8px; color: #c4cdf2; }
  p.sub { color: var(--muted); margin: 0 0 20px; font-size: 13.5px; }
  .meta { color: var(--muted); font-size: 13px; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin: 18px 0; }
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; }
  .card .k { color: var(--muted); font-size: 12.5px; }
  .card .v { font-size: 22px; font-weight: 700; margin-top: 2px; }
  .card .v.small { font-size: 16px; }
  .hero { display: flex; gap: 28px; align-items: center; flex-wrap: wrap; margin-top: 10px; }
  .score-big { font-size: 64px; font-weight: 800; color: var(--accent); line-height: 1; }
  .verdict { background: linear-gradient(160deg, rgba(99,102,241,.12), rgba(13,17,23,0));
    border: 1px solid var(--border); border-radius: 14px; padding: 16px 20px; margin: 14px 0 6px; }
  table { width: 100%; border-collapse: collapse; margin: 10px 0 16px; font-size: 13.5px; }
  th, td { border: 1px solid var(--border); padding: 8px 12px; text-align: left; vertical-align: top; }
  th { background: var(--panel2); color: #c4cdf2; font-weight: 600; }
  td.num { text-align: center; white-space: nowrap; }
  .tag { display: inline-block; padding: 1px 9px; border-radius: 999px; font-size: 12px; margin-right: 6px; border: 1px solid var(--border); }
  .tag.good { color: var(--good); border-color: rgba(52,211,153,.4); }
  .tag.warn { color: var(--warn); border-color: rgba(251,191,36,.4); }
  .tag.bad { color: var(--bad); border-color: rgba(248,113,113,.45); }
  .shot { display: block; width: 100%; border: 1px solid var(--border); border-radius: 10px; margin: 10px 0 4px; }
  .shot-cap { color: var(--muted); font-size: 12.5px; margin: 0 0 14px; }
  .pair { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  @media (max-width: 900px) { .pair { grid-template-columns: 1fr; } }
  ul { margin: 6px 0 12px; padding-left: 22px; }
  li { margin: 5px 0; }
  .li-good::marker { content: '✅ '; }
  .li-miss::marker { content: '⚠️ '; }
  .li-idea::marker { content: '💡 '; }
  code { background: var(--panel2); border: 1px solid var(--border); border-radius: 5px; padding: 1px 6px; font-size: 12.5px; }
  .foot { color: var(--muted); font-size: 12.5px; margin-top: 48px; border-top: 1px solid var(--border); padding-top: 16px; }
</style>
</head>
<body>
<div class="wrap">

  <h1>kjxy-Agent 五维度综合审查报告</h1>
  <p class="sub">审查对象：科技小院多智能体平台 <code>v3.70.0</code>（commit 51dd17e，2026-11-07）·
  范围：backend/app 11.8 万行 · frontend/src 8.9 万行 · client/desktop（Tauri 壳）· 415 个 API 路径 · 47 个业务模块</p>

  <!-- ================= 总览 ================= -->
  <div class="hero">
    <svg width="400" height="380" viewBox="0 0 400 392">${rings}${axes}${dataPoly}${dataDots}${labels}</svg>
    <div style="flex:1; min-width: 280px;">
      <div class="score-big">${overall}<span style="font-size:22px; color:var(--muted); font-weight:400;"> / 10</span></div>
      <p class="meta">五维平均分。分项：代码工程 ${SCORES[0].score} · 产品 ${SCORES[1].score} · 重度用户 ${SCORES[2].score} · 小白 ${SCORES[3].score} · 运营 ${SCORES[4].score}</p>
      <div class="cards">
        <div class="card"><div class="k">自动化测试</div><div class="v">2446</div><div class="k">后端全量绿</div></div>
        <div class="card"><div class="k">深度扫描</div><div class="v">86 页次 0 异常</div><div class="k">四角色五重检测</div></div>
        <div class="card"><div class="k">安全审计</div><div class="v">40+ 项确认</div><div class="k">遗留全部闭环</div></div>
        <div class="card"><div class="k">前端门禁</div><div class="v">0 error</div><div class="k">eslint · vue-tsc · build</div></div>
      </div>
    </div>
  </div>

  <div class="verdict">
    <strong>总裁定：可交付、可商用的一期产品。</strong>工程质量达到大厂主流水准（测试面、静态门禁、安全审计、契约纪律均有体系）；
    产品完成度超出典型科研院所信息化项目的平均线一大截（指挥官执行-复盘-测评-进化闭环是全行业少有的完整落地）。
    主要短板不在「有没有」，而在「好不好卖」：<b>支付闭环缺失、开放生态未成体系、ROI 量化故事没有产品化出口</b>——这三件事决定了它是「好工具」还是「好生意」。
  </div>

  <!-- ================= 维度一 ================= -->
  <h2>一、资深开发工程师视角 · ${SCORES[0].score} / 10</h2>
  <p class="sub">系统级审定：执行效率、死代码、并发正确性、测试与门禁体系、可维护性</p>

  <h3>做得好的（达到/超出大厂标准）</h3>
  <ul>
    <li class="li-good"><b>测试面覆盖真实充分</b>：后端 pytest 2446 个（本轮新增天气摘要 3 例后全量绿，5 分 49 秒），
      前端 node 单测 69 个 + Playwright E2E 48 个 spec；迁移 0058–0075 upgrade→downgrade 往返对称可逆。</li>
    <li class="li-good"><b>静态门禁成体系</b>：ruff / mypy（450 文件 0 问题）/ eslint 0 error / vue-tsc / vite build /
      npm audit 0 漏洞 / bandit 全库扫描 / 契约漂移检测（<code>make contract</code> + openapi 415 路径与代码强制一致）/
      <code>check_versions.py</code> 九处版本面一键校验。CI 全跑。</li>
    <li class="li-good"><b>死代码基本为零（本轮实测）</b>：业务代码 TODO/FIXME 0 处（16 处 TODO 全部位于「智能体脚手架模板」内——
      那是生成给用户填空的占位符，属于产品本身）；console.log 0；API 进程内 print 0（仅运维脚本与脚手架 CLI 合法使用）；
      前端无 v-html、markdown 全走 SafeMarkdown 转义；交付报告三轮扫描死组件 0、未挂载 router 0。</li>
    <li class="li-good"><b>历史问题闭环有据可查</b>：2026-11-03 审计报告的 P0/P1 全零，P2/P3 共 40+ 项按 v3.53→v3.57 分批修复并附回归测试，
      包括 XSS 面、SSRF 纵深（DNS 建连钉定）、任务幂等键时间桶、commander 原子占位/记账口径、配额迁 Redis 等——
      「审计→整改→复核」的工程纪律在大厂内部也不常见。</li>
    <li class="li-good"><b>执行效率有量化意识</b>：vite manualChunks 主业务包 1310KB→363KB、echarts 按需引入、路由全动态 import；
      N+1 用 AST 扫描定性（33 候选仅 1 真且不值得改）；probe-all 串行 150s→并发 20s；SSE 流语义完整（断点续传/心跳/超时收尾）。</li>
  </ul>

  <h3>不足与风险（按严重度）</h3>
  <ul>
    <li class="li-miss"><b>超大文件</b>：<code>commander/service.py 2778 行</code>、<code>chat/service.py 2162</code>、
      <code>supplier_service.py 2043</code>、<code>library/service.py 2024</code>——单文件承载过多职责，
      改一处要全懂一处，评审与并行协作成本持续上涨。建议按「计划/调度/执行/学习」切分 commander。</li>
    <li class="li-miss"><b>多实例语义仍有残留短板</b>：全库无 <code>with_for_update</code> 行锁，核心配额/限流已迁 Redis，
      但登录限流、搜索源熔断等仍为进程内状态（单机/单副本部署无碍，多副本需按部署文档核对）。</li>
    <li class="li-miss"><b>仓库卫生小尾巴</b>：<code>scripts/net_full_check.py</code> 存量 15 处 ruff 报错（另一会话交付物），
      与「全绿门禁」口径不符；根目录多份密钥文本文件依赖 .gitignore 兜底，建议移入密钥管理。</li>
    <li class="li-miss"><b>性能压测缺位</b>：SSE 高并发连接下的 session 轮询频率、长会话无虚拟化（FE-13）、
      SQLite 写锁并发上限——都没有真实压测数据背书。功能对，容量不明。</li>
    <li class="li-idea">建议补一份 <b>ADR 目录</b>（关键决策如 ADR-0003 出网策略已散落在代码注释里）与
      <b>性能预算表</b>（接口 P95 / 首屏 LCP 上限），把隐性标准显性化。</li>
  </ul>

  <table>
    <tr><th>门禁项</th><th>结果</th><th>门禁项</th><th>结果</th></tr>
    <tr><td>pytest 全量</td><td class="num">2446 passed</td><td>mypy</td><td class="num">0 error / 450 文件</td></tr>
    <tr><td>ruff（app+tests）</td><td class="num">0 error</td><td>eslint</td><td class="num">0 error（warning 存量）</td></tr>
    <tr><td>vue-tsc + vite build</td><td class="num">通过</td><td>npm audit / bandit</td><td class="num">0 漏洞 / 0 真实 High</td></tr>
    <tr><td>契约一致性</td><td class="num">415 路径对齐</td><td>版本面九处</td><td class="num">3.70.0 对齐</td></tr>
    <tr><td>业务代码 TODO / console.log / print</td><td class="num">0 / 0 / 0</td><td>死组件 / 未挂载路由</td><td class="num">0 / 0</td></tr>
  </table>

  <!-- ================= 维度二 ================= -->
  <h2>二、资深产品经理视角 · ${SCORES[1].score} / 10</h2>
  <p class="sub">高维认知：战略定位、能力完整度、差异化壁垒、下一步该补什么</p>

  <h3>产品是什么（一句话）</h3>
  <p>面向<b>科技小院（农业科研驻点场景）</b>的多智能体工作操作系统：把「人管人 + 人盯数据」替换为
  「指挥官 Agent 拆任务、助手执行、专家复核、日志沉淀、迭代进化」的闭环，并配上桌宠等情感化界面降低使用门槛。</p>

  <h3>能力全景（实测确认）</h3>
  <table>
    <tr><th>能力域</th><th>已落地（截图为证）</th><th>成熟度</th></tr>
    <tr><td>多智能体执行</td><td>指挥官工作台：预算/成功率/成本、日复盘、周测评 90.5 分五维画像、人格版本回滚、记忆检索</td><td class="num">高</td></tr>
    <tr><td>一句话生成</td><td>智能体/技能/插件同构生成链路（联网取证→LLM 严格 JSON→兜底模板）</td><td class="num">高</td></tr>
    <tr><td>农业场景</td><td>台账 103 地块、IoT 设备预警→自动转工单、拍照诊断、用药安全期</td><td class="num">高</td></tr>
    <tr><td>组织与治理</td><td>四角色千人千面、权限矩阵、日志工作台年度达标牵引、跨院集群监管、省级枢纽层级</td><td class="num">中高</td></tr>
    <tr><td>知识资产</td><td>知识库、技能市场、工作流自动萃取、记忆/经验资产</td><td class="num">中</td></tr>
    <tr><td>端与分发</td><td>桌面客户端（DMG/EXE + 应用内自动更新）、桌宠、语音 ASR、亮暗双主题、白标品牌包</td><td class="num">中高</td></tr>
  </table>

  <h3>值得发扬光大（护城河）</h3>
  <ul>
    <li class="li-good"><b>指挥官「执行-复盘-测评-进化」闭环</b>：周测评五维打分 + 人格快照回滚，等于给 Agent 建了绩效考核与版本管理——这是同类产品（Coze/Dify/通用 Agent 平台）都没有的纵深，应作为对外叙事的第一卖点。</li>
    <li class="li-good"><b>诚实占位原则</b>：官方模型支付明确标注「不完成真实支付」而不假装成功——这种克制在演示驱动 sales 的行业里是长期信任资产。</li>
    <li class="li-good"><b>情感化界面</b>：桌宠轮播小院实况（天气/工单/警报/任务结果）把「系统通知」变成了「伙伴转述」，是低文化门槛场景（基层农技员）的正确设计。</li>
  </ul>

  <h3>缺什么（按优先级）</h3>
  <ul>
    <li class="li-miss"><b>支付与订单闭环（唯一的功能性占位）</b>：模型购买、配额加油包都到「下单」为止。不闭环则客户无法自助续费，运营只能靠人肉收款。</li>
    <li class="li-miss"><b>移动端缺席</b>：科技小院的真实工作场景一半在田间，拍照诊断已有，但「队长在地铁上批工单」没有出口。小程序或 PWA 至少补一个。</li>
    <li class="li-miss"><b>对外开放不成体系</b>：已有 MCP/A2A/配对协议底座，但没有开发者门户、API 计费文档、Webhook 事件目录——生态价值兑现不了。</li>
    <li class="li-miss"><b>数据大屏 / 汇报出口弱</b>：给厅局领导看的大屏与一键汇报 PPT 是这类 To G/To B 项目的隐性刚需；目前只有周报生成与分享链接（farm_report/diagnosis 两类）。</li>
    <li class="li-idea">北极星指标建议定为「<b>每周被指挥官闭环的任务数 / 院</b>」，所有运营动作向它对齐。</li>
  </ul>

  <img class="shot" src="${img('06-commander')}" alt="指挥官工作台">
  <p class="shot-cap">指挥官工作台实拍：预算执行 10%、任务成功率 96%、近 30 天成本 ¥3.42、周测评 90.5 分（五维画像）、人格版本回滚——产品最深的护城河。</p>

  <!-- ================= 维度三 ================= -->
  <h2>三、重度使用客户视角 · ${SCORES[2].score} / 10</h2>
  <p class="sub">他们是谁：小院负责人、驻点研究生、省级枢纽运营者——未来付费与转介绍的主力</p>

  <h3>用得顺手的（会形成依赖）</h3>
  <ul>
    <li class="li-good"><b>今日工作台的「行动按钮」</b>：记一笔农事 / 拍照诊断 / 上报警报 / 开始对话 / 生成周报——高频动作一跳直达，日活摩擦极低。</li>
    <li class="li-good"><b>一句话派活</b>：把待处理工单按紧急度排序并派给助手→生成计划卡→执行→产物回收到会话，全程不用填表单。</li>
    <li class="li-good"><b>成本透明</b>：指挥官页直接给出近 30 天 ¥3.42、1132 次调用、单 run token 明细——客户敢放心用，才谈得上依赖。</li>
    <li class="li-good"><b>桌宠实况轮播</b>：天气/工单/警报/后台任务结果主动播报，人不用盯屏幕等结果。</li>
    <li class="li-good"><b>历史与记忆永续</b>：专属会话一直往下长、跨设备不丢；指挥官按问题族检索历史复盘——用得越久越值钱，这正是依赖感的来源。</li>
  </ul>

  <h3>不顺手的（流失风险点）</h3>
  <ul>
    <li class="li-miss"><b>工单积压无提醒升级</b>：演示库 18 张已逾期工单安静躺在列表里——重度用户需要「逾期自动升级 + 桌宠红色警报 + IM/推送」的强提醒链路（push 模块已有底座）。</li>
    <li class="li-miss"><b>成果出不去</b>：PPT/文档生成后锁在会话里；周报只能生成不能定时投递到邮箱/企业微信——重度用户的「汇报刚需」没有被产品化。</li>
    <li class="li-miss"><b>批量与效率操作浅</b>：批量会话入口有了，但工单批量指派、地块批量导入、记录批量补录仍逐条操作。</li>
  </ul>

  <h3>付费转化与依赖感设计（建议）</h3>
  <ul>
    <li class="li-idea"><b>免费额度 → 订阅的阶梯</b>：当前配额体系（模型调用/知识文档/后台任务三维）已能支撑「按院订阅 + 超量加油包」；
      建议免费版封顶「1 院 + 3 助手 + 500 次/月」，把「指挥官测评/人格进化」「自动转工单」放进付费档——为「效率与自动化」付费，而非为「能用」付费。</li>
    <li class="li-idea"><b>依赖感飞轮</b>：知识库 + 农事记录 + 指挥官记忆 = 客户的数据资产沉淀在平台。建议做一个「资产仪表盘」：
      本院已沉淀 N 条农事记录 / M 篇诊断方案 / K 个可复用工作流，累计节省约 X 小时——离开成本可视化，续费率自然上来。</li>
    <li class="li-idea"><b>专家复用网络</b>：专家复核过的防治方案可（经授权）进入区域模板库，枢纽账号抽成或积分——重度专家有收益，平台有供给。</li>
  </ul>

  <img class="shot" src="${img('03-home-dark')}" alt="今日工作台">
  <p class="shot-cap">管理员首页实拍：32 张待处理工单（18 逾期）、20 条诊断待复核、28 条未处理警报全部数字前置——重度用户一眼知道今天该干什么。</p>

  <!-- ================= 维度四 ================= -->
  <h2>四、小白用户视角 · ${SCORES[3].score} / 10</h2>
  <p class="sub">标准：不看手册、没人带，第一次能不能把事办成</p>

  <h3>友好的</h3>
  <ul>
    <li class="li-good"><b>初始化向导（v3.69）</b>：全新部署零用户时六步引导建超管/小院/画像/品牌/开关，全程不需要看文档——这是小白路径上最关键的一块拼图。</li>
    <li class="li-good"><b>菜单千人千面</b>：研究生账号只看到「概览/对话/知识库/任务中心」四项（见下图），不会被 20 个管理菜单吓退。</li>
    <li class="li-good"><b>空态有话术</b>：对话页空态给四条可点示例；桌宠主动打招呼；输入框占位文案即教程（「说要办的事，Enter 交给指挥官」）。</li>
    <li class="li-good"><b>图文手册兜底</b>：87 张四角色实拍截图的说明书随仓库交付，搜「用户操作手册.md」即可。</li>
  </ul>

  <h3>会卡住的</h3>
  <ul>
    <li class="li-miss"><b>登录页即终点</b>：未注册用户只看到「开通新的小院」一个链接，注册入口、演示账号提示都很弱——如果商业模式含自助开通，这里流失率会很高。</li>
    <li class="li-miss"><b>概念密度高</b>：指挥官/班组/助手/技能/插件/工作流/枢纽/智能体工厂——小白第一周至少遇到 8 个新词。建议首次登录强制一段 30 秒的「带练」（发一句话→看计划卡→收产物）。</li>
    <li class="li-miss"><b>数字仪表盘对小白过载</b>：首页同时呈现工单/诊断/地块/配额/设备洞察 5 组数据，基层农技员真正需要的可能只有「今天要不要浇水打药」。</li>
    <li class="li-idea">低成本改法：按角色配「首页预设」——研究生默认只显示待办与快捷动作，管理员才看全量仪表盘；再配一个「第一次来这里？」的 3 步卡片。</li>
  </ul>

  <div class="pair">
    <div>
      <img class="shot" src="${img('01-login')}" alt="登录页">
      <p class="shot-cap">小白第一眼：登录页干净，但注册/演示账号引导弱。</p>
    </div>
    <div>
      <img class="shot" src="${img('20-operator-home')}" alt="低权限角色首页">
      <p class="shot-cap">低权限研究生首页：菜单千人千面已做到位，但信息密度仍偏高。</p>
    </div>
  </div>

  <!-- ================= 维度五 ================= -->
  <h2>五、运营经理视角 · ${SCORES[4].score} / 10</h2>
  <p class="sub">如何让产品创造更大的经济价值、给客户超出投入的回报</p>

  <h3>变现路径盘点（按可行性排序）</h3>
  <table>
    <tr><th>路径</th><th>现成底座</th><th>缺口</th><th>建议定价锚点</th></tr>
    <tr><td>按院订阅（SaaS）</td><td>多租户/权限/配额三维限制</td><td>支付闭环、套餐档位页</td><td>基础院 ¥X 千/年；旗舰院含指挥官测评</td></tr>
    <tr><td>配额加油包 / 官方模型差价</td><td>配额体系、模型中心、订单占位</td><td>支付与自动到账</td><td>按 token 加价 20–40%</td></tr>
    <tr><td>私有化 License + 年度维保</td><td>桌面客户端/内网部署/集群监管</td><td>License 发放与校验工具</td><td>License + 15–20% 年维保</td></tr>
    <tr><td>省级枢纽平台（To G）</td><td>枢纽层级/白标品牌包/一院多枢纽</td><td>大屏、汇报出口、省级运营指标</td><td>项目制，客单价最高</td></tr>
    <tr><td>技能/模板市场分成</td><td>技能市场、导入审批、配额</td><td>作者结算与审核运营</td><td>平台抽成 20–30%</td></tr>
  </table>

  <h3>客户 ROI 故事（用系统自己的数据讲）</h3>
  <ul>
    <li class="li-idea"><b>效率账</b>：一次拍照诊断 + 防治方案生成，人工约半天、系统几分钟；32 张工单自动分派催办 ≈ 每周省 1 个人日；周报/日复盘自动生成 ≈ 每月省 2 个人日。折算人力成本即可算出「回本周期」。</li>
    <li class="li-idea"><b>风险账</b>：严重警报自动转工单 + 逾期升级 = 把「漏看一条警报毁一季作物」的尾部风险货币化——对院长，这句话比省时间更值钱。</li>
    <li class="li-idea"><b>资产账</b>：专家复核过的方案沉淀为区域知识资产，新院开箱即用——买的是「整个科技小院网络的经验」，不是软件。</li>
    <li class="li-miss"><b>缺一个 ROI 看板</b>：上述三笔账系统里数据都有（工单闭环率、诊断时效、token 成本、复盘节省），但没有汇成一页「投入产出」报表给客户院长看。建议两周内做出来，它会同时提升续费率与客单价。</li>
  </ul>

  <img class="shot" src="${img('14-quota')}" alt="用量额度看板">
  <p class="shot-cap">用量额度看板：模型调用/知识文档/后台任务三维配额实时可视——订阅制与加油包的计费底座已经就绪，差的只是支付与套餐页。</p>

  <!-- ================= 结论 ================= -->
  <h2>结论与行动清单</h2>
  <table>
    <tr><th>优先级</th><th>事项</th><th>负责视角</th><th>预估量</th></tr>
    <tr><td class="num"><span class="tag bad">P0</span></td><td>支付/订单闭环 + 套餐档位页（解锁所有变现路径）</td><td>产品 + 运营</td><td>1–2 周</td></tr>
    <tr><td class="num"><span class="tag bad">P0</span></td><td>ROI 一页看板（工单闭环率/诊断时效/节省人时/成本）</td><td>运营 + 前端</td><td>2 周</td></tr>
    <tr><td class="num"><span class="tag warn">P1</span></td><td>工单逾期升级 + 推送链路（push 底座已有）</td><td>重度用户</td><td>1 周</td></tr>
    <tr><td class="num"><span class="tag warn">P1</span></td><td>小白首次带练 + 角色化首页预设</td><td>小白</td><td>1–2 周</td></tr>
    <tr><td class="num"><span class="tag warn">P1</span></td><td>commander/service.py 等超大文件拆分</td><td>工程</td><td>渐进</td></tr>
    <tr><td class="num"><span class="tag good">P2</span></td><td>移动端（PWA/小程序）、开发者门户、数据大屏</td><td>产品</td><td>排期</td></tr>
    <tr><td class="num"><span class="tag good">P2</span></td><td>性能压测报告（SSE 并发/长会话）+ ADR 目录</td><td>工程</td><td>排期</td></tr>
  </table>

  <div class="verdict">
    <strong>一句话收束：</strong>这套系统的「内功」（工程纪律、场景纵深、成本透明）已经超出目标客群的预期；
    下半场比的不是功能多少，而是<b>把价值变成客户看得见的钱</b>——支付闭环、ROI 看板、强提醒链路三件事做完，
    它就从「好用的工具」变成「离不开的生意」。
  </div>

  <div class="foot">
    审查方法：① 全量自动化门禁实测（pytest 2446 / mypy / eslint / build / 契约 / 版本面）；② 死代码与卫生专项 grep 实测；
    ③ 复核 2026-11-03 审计报告（371 行）与 2026-11-06 交付检测报告的全部结论及整改闭环记录；
    ④ 前后端升至 v3.70.0 后四角色 × 20 关键页面实拍（<code>docs/review-shots/</code>，脚本 <code>frontend/scripts-review-shots.mjs</code>）；
    ⑤ 产品/运营判断基于 CHANGELOG 全量（v1.x→v3.70）、两份行业调研报告与功能实测。
    本报告由 <code>frontend/scripts-review-report.mjs</code> 生成，截图以 base64 内嵌，单文件自包含。
  </div>
</div>
</body>
</html>
`

writeFileSync(outPath, html)
console.log(`已生成：${outPath}（${(html.length / 1024 / 1024).toFixed(1)} MB）`)
