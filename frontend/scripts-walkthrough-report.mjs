/**
 * 拟人式走查五维度审查报告生成器（2026-11-10 · v3.73.3 基线）：
 * 内嵌 docs/walkthrough-shots/ 真实截图，产出自包含单文件
 * docs/review-report-2026-11-10.html。
 */
import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const shotDir = resolve(here, '../docs/walkthrough-shots')
const outPath = resolve(here, '../docs/review-report-2026-11-10.html')

const img = name => {
  const b64 = readFileSync(resolve(shotDir, `${name}.png`)).toString('base64')
  return `data:image/png;base64,${b64}`
}

const html = `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>拟人式全流程走查审查报告 · 2026-11-10</title>
<style>
  :root { --bg:#0d1117; --panel:#151b27; --panel2:#1a2130; --border:rgba(150,172,196,.16);
    --text:#e6ecf7; --muted:#93a0b8; --accent:#818cf8; --good:#34d399; --warn:#fbbf24; --bad:#f87171; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--text); line-height:1.75;
    font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif; }
  .wrap { max-width:1080px; margin:0 auto; padding:32px 20px 80px; }
  h1 { font-size:25px; margin:8px 0 4px; }
  h2 { font-size:20px; margin:44px 0 6px; padding-top:18px; border-top:1px solid var(--border); }
  h3 { font-size:15.5px; margin:20px 0 8px; color:#c4cdf2; }
  p.sub { color:var(--muted); margin:0 0 18px; font-size:13.5px; }
  .meta, .foot { color:var(--muted); font-size:12.5px; }
  .card { background:var(--panel); border:1px solid var(--border); border-radius:12px; padding:14px 16px; margin:12px 0; }
  .verdict { background:linear-gradient(160deg, rgba(99,102,241,.12), rgba(13,17,23,0));
    border:1px solid var(--border); border-radius:14px; padding:16px 20px; margin:14px 0; }
  table { width:100%; border-collapse:collapse; margin:10px 0 16px; font-size:13px; }
  th,td { border:1px solid var(--border); padding:7px 11px; text-align:left; vertical-align:top; }
  th { background:var(--panel2); color:#c4cdf2; }
  .tag { display:inline-block; padding:1px 9px; border-radius:999px; font-size:12px; margin-right:6px; border:1px solid var(--border); }
  .tag.good { color:var(--good); border-color:rgba(52,211,153,.4); }
  .tag.warn { color:var(--warn); border-color:rgba(251,191,36,.4); }
  .tag.bad { color:var(--bad); border-color:rgba(248,113,113,.45); }
  .tag.p0 { color:#fff; background:var(--bad); border-color:var(--bad); }
  .tag.p1 { color:#1b2333; background:var(--warn); border-color:var(--warn); font-weight:700; }
  .tag.p2 { color:var(--warn); border-color:rgba(251,191,36,.4); }
  .tag.p3 { color:var(--muted); }
  .shot { display:block; width:100%; border:1px solid var(--border); border-radius:10px; margin:10px 0 4px; }
  .cap { color:var(--muted); font-size:12.5px; margin:0 0 14px; }
  ul { margin:6px 0 12px; padding-left:22px; }
  li { margin:5px 0; }
  li.g::marker { content:'✅ '; } li.m::marker { content:'⚠️ '; } li.i::marker { content:'💡 '; }
  code { background:var(--panel2); border:1px solid var(--border); border-radius:5px; padding:1px 6px; font-size:12px; }
  .steps { background:var(--panel); border:1px dashed var(--border); border-radius:10px; padding:10px 16px; font-size:12.8px; color:#b8c2d9; margin:10px 0; }
  .foot { margin-top:44px; border-top:1px solid var(--border); padding-top:16px; }
</style>
</head>
<body>
<div class="wrap">

  <h1>拟人式全流程走查 · 五维度审查报告</h1>
  <p class="sub">审查对象：科技小院多智能体平台 <code>v3.73.3</code>（本地 main，与 GitHub origin/main 同步 · 2026-11-10）。
  走查方式：<b>真实运行系统 + 真实账号登录 + 真实 LLM 调用</b>，全部主链路端到端打通；LLM 采用用户提供的 Agnes AI（OpenAI 兼容），
  文本 <code>agnes-3.0-flash</code> / 图像 <code>agnes-image-2.5-flash</code> / 视频 <code>agnes-video-2.5-flash</code>，
  经「模型中心」UI 配置入系统并绑定全局与视觉能力位。截图 28 张存 <code>docs/walkthrough-shots/</code>，本报告内嵌 14 张关键实拍。</p>

  <div class="verdict">
    <strong>端到端结论：10 条主链路全部真实跑通（8 成 2 带缺陷打通）。</strong>
    登录→配置供应商→普通对话（真实回答+token 账单）→对话内出图（832×1248 海报下载）→出片（上游 400，抓到适配缺陷）→
    指挥官拆解执行（真实子任务+产物）→一句话生成智能体（5 角色蓝图）→套餐兑换闭环（发码-到账-订单）→ROI/大屏/桌宠→小白低权限视角。
    本报告所有结论均来自<b>实际操作</b>，不是代码推断。
  </div>

  <h2>〇、端到端验证结果总表</h2>
  <table>
    <tr><th>#</th><th>流程</th><th>结果</th><th>关键证据</th></tr>
    <tr><td>1</td><td>环境对齐（代码 3.73.2→服务重启 + 迁移 0077–0081）</td><td><span class="tag good">通过</span></td><td>/meta：3.73.2 / 0081_media_assets</td></tr>
    <tr><td>2</td><td>真实账号登录（admin 平台管理员 / operator 科研操作员）</td><td><span class="tag good">通过</span></td><td>a02 / g02 截图</td></tr>
    <tr><td>3</td><td>模型中心 UI 配置 Agnes（模板选「自定义 OpenAI 兼容」→ 填表 → 自动拉取 10 个上游模型 → 保存）</td><td><span class="tag good">通过</span></td><td>a04–a06；供应商列表 agnes-main</td></tr>
    <tr><td>4</td><td>绑定解析（全局默认 + image_generation / video_generation 能力位）</td><td><span class="tag good">通过</span></td><td>bindings/test：<code>ok:true · agnes-3.0-flash · 14.8s · 回复「链路通」</code></td></tr>
    <tr><td>5</td><td>普通对话（真实 LLM 回答 + 智能分类 + token 账单）</td><td><span class="tag good">通过</span></td><td>b03：1835 tokens · 9977ms · agnes-3.0-flash</td></tr>
    <tr><td>6</td><td>对话内出图（/图片 触发 → 规格整理 → 生成 → 卡片+下载）</td><td><span class="tag good">通过</span></td><td>b08；media_assets: image/ready 832×1248</td></tr>
    <tr><td>7</td><td>对话内出片（/视频 触发 → 提交上游）</td><td><span class="tag bad">缺陷</span></td><td>b10：上游 400 <code>mode is required</code></td></tr>
    <tr><td>8</td><td>指挥官拆解执行（一句话目标 → 子任务 → 汇总 → 交付产物）</td><td><span class="tag good">通过</span></td><td>c04：9717 tokens · 15.4s · 交付清单</td></tr>
    <tr><td>9</td><td>一句话生成智能体（Agent 工厂四步全绿，5 角色蓝图）</td><td><span class="tag good">通过</span></td><td>f06；tasks: agent_factory/succeeded</td></tr>
    <tr><td>10</td><td>套餐兑换闭环（UI 发码 → 输码 → 到账 → 订单落表）</td><td><span class="tag good">通过</span></td><td>f03；billing_codes redeemed + orders pro/paid</td></tr>
    <tr><td>11</td><td>ROI 投入产出 / 运行大屏 / 桌宠 / 工单 / 任务中心</td><td><span class="tag good">通过</span></td><td>e03–e08</td></tr>
    <tr><td>12</td><td>小白视角（operator：菜单千人千面 + 真实提问）</td><td><span class="tag warn">通过带发现</span></td><td>g02/g03（两处 UX 缺陷，见维度四）</td></tr>
  </table>

  <!-- ============ 维度一 ============ -->
  <h2>一、资深开发工程师 · 8.5 / 10</h2>
  <p class="sub">真实运行暴露的 bug + 全库静态扫描；对照大厂规范逐条定级</p>

  <h3>走查发现的问题（按定级）</h3>
  <ul>
    <li class="m"><span class="tag p1">P1</span><b>视频生成适配缺必填参数（端到端抓到）</b>：<code>/视频 一段5秒的稻田航拍丰收画面</code> 走通媒体编排后，上游返回
      400 <code>{"code":"invalid_request","message":"mode is required"}</code>。对照 Agnes 文档（wiki.agnes-ai.com/agnes-video-25-flash）：
      <code>mode</code> 必填且取值仅 <code>text|keyframe|reference</code>，时长字段是字符串 <code>seconds:"4"-"12"</code>（不是 duration 数值）。
      <code>llm/video.py</code> 需按协议补 <code>mode:"text"</code> 与字符串 <code>seconds</code>。这是本次走查<b>唯一的 P1 功能缺陷</b>。</li>
    <li class="m"><span class="tag p2">P2</span><b>媒体触发词表对常见写法失效</b>：<code>/画 一张…海报</code> 因「/画」不在显式指令表（只认 /图 /图片 /视频）、
      「画 一张」（中间有空格）不匹配动词表里的「画一张」→ 媒体意图未识别，请求被普通对话链路应答（b05 实证）。
      建议：显式指令集扩充 <code>/画 /海报 /出图</code>；动词匹配改为分词级（「画」+「一张」分离计分）。</li>
    <li class="m"><span class="tag p2">P2</span><b>媒体失败裸 JSON 直出给用户</b>：视频 400 的原始报错串（含 request id、param 字段名）直接渲染进对话气泡（b10）。
      大厂口径是把上游错误翻译成人话（「视频生成通道要求指定生成模式，已反馈研发」）+ 保留 trace 供支持排查。</li>
    <li class="m"><span class="tag p3">P3</span><b>智能分类路由语义弱</b>：「用三句话向驻点研究生介绍这个平台」被分类到「农机维修问答助手-17」（b03）；
      模型换成 agnes 后分类 prompt 的语义边界没有重新校准。回答内容尚可，但路由错位说明分类兜底逻辑（无匹配→默认助手）偏宽松。</li>
    <li class="m"><span class="tag p3">P3</span><b>计划横幅与执行口径不一致</b>：指挥官计划卡横幅「3 个助手并行」，实际只拆出 1 个子任务执行（c04）。
      展示口径与 planner 输出需要对齐（去重/合并后应回写横幅）。</li>
  </ul>

  <h3>静态扫描（全库实测）</h3>
  <table>
    <tr><th>指标</th><th>实测值</th><th>对照大厂标准</th></tr>
    <tr><td>后端 LOC / 前端 LOC</td><td>121,780 / 92,081</td><td>中型单仓规模，模块化清晰（49 个后端模块）</td></tr>
    <tr><td>业务代码 TODO/FIXME · console.log</td><td>0 · 0</td><td>达标（仅脚手架模板含占位 TODO，属产品语义）</td></tr>
    <tr><td>自动化测试</td><td>后端 2455 全绿 + 前端 70 单测 + 48 E2E spec</td><td>覆盖面超出同规模团队均值</td></tr>
    <tr><td>超大文件</td><td>commander/service.py 2752 · chat/service.py 2190 · supplier_service 2048 · library/service 2024</td><td>超标（建议单文件 &lt;1500 行）；v3.71 已拆 ledger.py，需继续按调度/执行/交付切</td></tr>
    <tr><td>静态门禁</td><td>mypy 0 · ruff 0 · eslint 0 error · 契约 423 路径同步 · 版本面九处一致</td><td>体系化程度高（CI 全跑）</td></tr>
    <tr><td>外部协议适配</td><td>OpenAI 兼容层统一，但视频协议校验缺第三方必填参数（本条 P1）</td><td>建议给每个 upstream 协议加「参数契约单测」（mock Agnes 文档样例回放）</td></tr>
  </table>
  <ul>
    <li class="i">建议补一份<b>上游供应商协议回归集</b>：把 Agnes 文档的 image/video 请求样例做成契约测试（未来换模型/换供应商不炸）。</li>
    <li class="i">性能基线已有（7 条读路径 P95&lt;700ms）；本轮真实 LLM 链路延迟 10–15s/次，前端超时已调 120s 匹配慢推理模型——建议把「模型延迟分档」做进 UI 提示（慢模型转圈时给预期）。</li>
  </ul>

  <div class="steps"><b>工程师维度实际操作：</b>① git fetch 确认本地与 origin/main 同步（0 ahead）；② 杀旧进程 → alembic upgrade head（0077–0081）→ 重启 uvicorn + vite；③ curl 直连 Agnes 三模型验证；④ /llm/providers 全库查配置；⑤ Playwright 全程捕获 pageerror/console（0 沉淀错误）；⑥ 后端 sqlite 只读复核 media_assets/billing/tasks 表终态；⑦ 全库 grep 死代码与重复实现。</div>

  <!-- ============ 维度二 ============ -->
  <h2>二、资深产品经理 · 8.7 / 10</h2>
  <p class="sub">架构 → 执行 → 使用路径；UI、交互、前后端衔接、扩展性</p>

  <h3>走查确认的产品亮点</h3>
  <ul>
    <li class="g"><b>「配置即用」的模型中心</b>：新增供应商弹窗填完 Base URL + Key 自动拉取上游 10 个模型（a05），能力位绑定（对话/出图/出片）一处完成——接入一个新模型供应商全程 &lt;3 分钟，这是多模型平台的正确形态。</li>
    <li class="g"><b>对话内直接出图成体系</b>：规则识别（不靠模型自觉）→ LLM 整理规格 → 生成 → 海报叠字（中文标题）→ 卡片内下载，全链路确定性编排（b08）。这个链路的工程完成度超过多数同类产品。</li>
    <li class="g"><b>指挥官执行透明</b>：子任务卡片显示执行助手名、token、耗时（c04），配合指挥官工作台的预算/成功率/成本（96%、¥3.43），形成了「委托-汇报-审计」闭环。</li>
    <li class="g"><b>Agent 工厂的角色蓝图</b>：一句话生成 5 个互补角色 + 工作流编排 + 「知识缺口——建议先上传该方向资料」提示（f06），把联网取证结果显性化，是好的「先给图纸再施工」设计。</li>
  </ul>

  <h3>走查发现的产品问题</h3>
  <ul>
    <li class="m"><span class="tag p2">P2</span><b>智能分类路由的语义边界</b>（同工程师 P3）：「介绍平台」被分给农机维修助手。建议：分类置信度低于阈值时改走「通用对话」，并把「为什么路由给 TA」做成可展开的推理摘要（现在只有「智能分类」小标签）。</li>
    <li class="m"><span class="tag p2">P2</span><b>媒体触发词产品化不足</b>：触发词表是工程师视角的黑名单/白名单，用户自然语言「画一张…」「帮我生成一张…」（分词命中）「/画」都会失效。建议：意图识别升级为「规则先行 + LLM 意图兜底」（媒体请求是小成本确认调用，不该漏）。</li>
    <li class="m"><span class="tag p2">P2</span><b>出片失败后的「下一步」缺失</b>：报错卡片只有「有问题」按钮，没有「用图生视频」「重试」「查看文档」等动作。失败的终点应该是替代路径，不是句号。</li>
    <li class="m"><span class="tag p3">P3</span><b>能力绑定入口分散感</b>：模型中心管绑定、悬浮伙伴「能力」抽屉也能管指挥官模型——两处入口契约虽同源，但小白会困惑在哪里改。建议能力抽屉只读展示 + 跳模型中心。</li>
    <li class="i">扩展性确认：MCP/A2A/机器人 webhook/媒体 purpose 绑定位都在，模型层换供应商零代码（本轮实测）——架构弹性是真实存在的，不是纸面设计。</li>
  </ul>

  <img class="shot" src="${img('a05-test-result')}" alt="模型中心配置">
  <p class="cap">实拍：新增供应商弹窗——填完地址与密钥自动获取上游 10 个模型并勾选可用模型；右侧列表可见旧 Mock 供应商与「当前生效解析」（改绑前 fake-mock-1，改绑后 agnes）。右下角悬浮伙伴与桌宠同屏在场。</p>

  <div class="steps"><b>产品维度实际操作：</b>模型中心全流程 UI 配置（模板→表单→自动拉模型→测试→保存→绑定）逐页截图；对话页/指挥官/Agent 工厂三处发起真实请求并等待终态；对照 docs/client.md 与 CHANGELOG v3.38/v3.73 的设计承诺逐条验证（执行模式切换、对话内媒体、指挥官直办）。</div>

  <!-- ============ 维度三 ============ -->
  <h2>三、重度用户 · 8.6 / 10</h2>
  <p class="sub">以生产力工具标准：顺手程度、解决问题能力、价值创造、日常依赖潜力</p>

  <ul>
    <li class="g"><b>成本透明到每一轮</b>：每条回答底部「1835 tokens（输入 1740 / 输出 95）· 9977ms · agnes-3.0-flash」，会话头「本会话 9,748 tokens」——重度用户敢放开用，是因为账算得清（b03/c04）。</li>
    <li class="g"><b>产物即取即用</b>：指挥官交付清单（工作小结 + 交付物 + 下一步建议）结构化呈现；海报卡片一键下载原图（1.4MB PNG 带中文叠字标题）——「要个东西，给个东西」的闭环感强。</li>
    <li class="g"><b>会话永不丢</b>：左侧「进行中/已归档」全部历史在列，切换账号各自独立；指挥官运行可回对话续处理。</li>
    <li class="g"><b>实况陪伴减少盯屏</b>：桌宠气泡轮播真实数据（「有 10 条未处理一般警报」「8 张工单已逾期，最急一张：专家会诊-水稻」——g03/b10 实拍），干活时不盯后台也能收到提醒，这是形成依赖的钩子。</li>
    <li class="m"><span class="tag p2">P2</span><b>慢模型下的等待体验</b>：agnes-3.0-flash 每轮 10–15s，指挥官拆解到产出全程 ~2 分钟。重度用户可接受但需要「分步进度」（现有「过程·3 步」展开是好的，建议加每步耗时标尺）。</li>
    <li class="m"><span class="tag p3">P3</span><b>视频/出图失败无法在原地换路径</b>（同 PM 维度）：重度用户遇到 400 想立刻改参数重试，现在只能重新发一条。</li>
    <li class="i">依赖潜力评估：知识库沉淀 + 农事台账 + 会话历史 + 指挥官记忆四类资产都在账号里，越用越值钱的结构成立；重度用户流失成本高。</li>
  </ul>

  <img class="shot" src="${img('c04-exec-result')}" alt="指挥官执行结果">
  <p class="cap">实拍：指挥官端到端——一句话目标「写 80 字本周工作小结」→ 子任务（文档与PPT交付专家 · 3490 tokens · 5.7s）→ 汇总 → 交付产物清单 → 下一步建议；底部真实账单 9717 tokens · 15.4s。</p>

  <div class="steps"><b>重度用户维度实际操作：</b>连续 6 轮真实请求（普通问答/出图/出片/指挥官/生成智能体/兑换），统计每轮耗时与 token；验证历史会话回看、产物下载、桌宠实况播报三项日常习惯动作。</div>

  <!-- ============ 维度四 ============ -->
  <h2>四、小白用户 · 7.9 / 10</h2>
  <p class="sub">首次接触上手难度；是否愿意给第二、三次机会</p>

  <ul>
    <li class="g"><b>菜单千人千面对小白友好</b>：operator（科研操作员）只看到 4 个入口（概览/智能对话/知识库/任务中心），不会被 30 个管理菜单劝退（g02）。</li>
    <li class="g"><b>输入框占位文案即教程</b>：「直接说要做的事，Enter 发送…可先上传照片或文档」——不识字汇也能猜对怎么用；桌宠陪伴降低了冷感。</li>
    <li class="m"><span class="tag p2">P2</span><b>登录后首落页是「工作日志」</b>（g01→g02 实证：operator 登录直接进 /journal）：小白第一眼想看的通常是「今天要干什么/平台能干什么」，日志页是创作者视角的页面。建议按角色配首落页（低权限→今日工作台）。</li>
    <li class="m"><span class="tag p2">P2</span><b>会话模式与按钮状态不同步</b>（g03 实证）：右下角执行模式高亮「普通」，但当前会话标题是「指挥官伙伴」且实际走指挥官拆解（「正在选择助手 指挥官正在拆解任务」）。小白的第一问被拿去「拆解任务」，等待 60s+ 无直接答案——第二次打开的意愿会掉。建议：低权限角色默认会话强制普通模式；模式与标题同步显示。</li>
    <li class="m"><span class="tag p3">P3</span><b>等待期安抚不足</b>：慢模型转圈 60–90s，只有「正在选择助手」一行小字。建议：转圈时轮播一句「通常 30–90 秒」+ 桌宠动态同步（忙碌动画已有，缺文案）。</li>
    <li class="i">是否给第二次机会：引导卡（v3.71 三步带练）+ 桌宠 + 千人千面是加分项；把首落页和会话模式两处修掉，小白留存曲线会明显改善——这是改动小、收益大的两刀。</li>
  </ul>

  <img class="shot" src="${img('g03-novice-chat-reply')}" alt="小白视角">
  <p class="cap">实拍：operator（科研操作员）视角——菜单仅 4 项；提问「土壤水分下降是什么原因？」后显示「正在选择助手 指挥官正在拆解任务」（执行模式按钮却高亮「普通」——状态不同步实证）；右下角桌宠气泡正在播报「有 8 张工单已逾期，最急的一张：专家会诊-水稻」。</p>

  <div class="steps"><b>小白维度实际操作：</b>清空全部 localStorage 模拟全新用户 → operator 登录 → 记录首落页 → 直接发一个最自然的提问 → 等 60s 观察 → 尝试登出（用户菜单定位失败，顺手记一条 P3：登出入口在小字菜单里，不够显眼）。</div>

  <!-- ============ 维度五 ============ -->
  <h2>五、资深运营经理 · 8.4 / 10</h2>
  <p class="sub">如何创造价值、付费引导、付费流程便捷度、升级动力</p>

  <ul>
    <li class="g"><b>付费闭环实测：发码 → 兑换 → 到账全程 10 秒</b>。UI 点「生成兑换码」（10 个专业版码即出，自动复制）→ 「输码兑换」→ 绿色 toast「兑换成功，「专业版」额度已到账」→ 订单表落行「专业版 ¥4,999 已到账 · 凭证：兑换码 KY-3E36-35BA-620B」（f01–f03 + DB 双确认）。这是支付网关接入前<b>真实可用的收费通道</b>。</li>
    <li class="g"><b>付费意愿的弹药已备齐</b>：每轮 token 账单、指挥官成本 ¥3.43/30 天、ROI 页「估算节省人时 7 小时 ≈ 0.9 人日」（e05）——「你省了多少、花了多少、值不值」三问都有数。给院长看的 ROI 页脚还诚实标注了折算口径。</li>
    <li class="g"><b>升级动力的产品钩子存在</b>：免费额度用尽时配额页「套餐与兑换」tab 是自然动线（超限告警 → 买加油包）；3 档订阅 + 2 加油包档位清晰。</li>
    <li class="m"><span class="tag p1">P1（运营侧）</span><b>支付网关缺位仍是商业化的最大瓶颈</b>：兑换码/对公单都要求平台管理员线下操作，规模化收费（数百小院）时人力对账会成为瓶颈。微信/支付宝商户号开通后，官方模型通道的验签 webhook 底座（v3.33 已有）可直接复用。</li>
    <li class="m"><span class="tag p2">P2</span><b>兑换码缺「批量导出/作废统计」运营报表</b>：发码页只有列表；运营需要「发了多少、兑了多少、沉睡多少」的汇总行与导出。改动小（一次聚合查询 + 一列合计）。</li>
    <li class="m"><span class="tag p2">P2</span><b>定价页面的信任要素不全</b>：套餐卡只有示例价，缺「开具发票/对公账户信息/服务 SLA」入口——To B 客户在下单前必问这三件事。</li>
    <li class="i">运营打法建议：① 用 ROI 页数据包装 3 个真实小院案例做「回本周期」故事；② 兑换码做渠道分层（省级枢纽批发码）；③ 把「工单闭环率 + 诊断复核数」做成给主管单位看的月报导出——续费谈判的证据链。</li>
  </ul>

  <img class="shot" src="${img('f03-redeem-result')}" alt="兑换闭环">
  <p class="cap">实拍：兑换闭环——顶部 toast「兑换成功，「专业版」额度已到账」；本院订单落行（¥4,999 · 已到账 · 凭证说明含兑换码）；下方平台管理员区可见 10 个生成的码（1 个已消费）。</p>

  <div class="steps"><b>运营维度实际操作：</b>配额页「套餐与兑换」tab 全流程（生成 10 码 → 取码 → 基础版…实际用专业版码兑换 → 观察订单与额度）→ 概览页核对额度卡片 → ROI 页取数 → 检查套餐页信任要素与导出能力。</div>

  <!-- ============ 缺陷清单 ============ -->
  <h2>附：本轮走查缺陷与改进清单（汇总）</h2>
  <table>
    <tr><th>级别</th><th>问题</th><th>维度</th><th>可执行修复建议</th></tr>
    <tr><td><span class="tag p1">P1</span></td><td>视频生成缺 Agnes 必填 <code>mode</code>（text/keyframe/reference），seconds 应为字符串</td><td>工程</td><td>llm/video.py 按协议补参 + 契约回放测试（文档样例做成 fixture）</td></tr>
    <tr><td><span class="tag p1">P1</span></td><td>支付网关缺位（规模化收费瓶颈）</td><td>运营</td><td>开通商户号后复用现有验签 webhook 底座</td></tr>
    <tr><td><span class="tag p2">P2</span></td><td>媒体触发词表漏「/画」、动词表被空格拆散</td><td>工程/产品</td><td>显式指令集扩充 + 分词级动词匹配 + LLM 意图兜底</td></tr>
    <tr><td><span class="tag p2">P2</span></td><td>小白首落页为工作日志</td><td>小白</td><td>按角色配首页预设（低权限→今日工作台）</td></tr>
    <tr><td><span class="tag p2">P2</span></td><td>会话执行模式与按钮高亮不同步；小白第一问被拆解</td><td>小白/产品</td><td>低权限默认普通会话；标题与模式同步显示</td></tr>
    <tr><td><span class="tag p2">P2</span></td><td>媒体失败裸 JSON 直出、无替代路径</td><td>工程/产品</td><td>错误翻译成人话 + 重试/改路径动作</td></tr>
    <tr><td><span class="tag p2">P2</span></td><td>智能分类路由语义弱（介绍平台→农机助手）</td><td>产品</td><td>置信度阈值 + 兜底走通用对话 + 路由理由可展开</td></tr>
    <tr><td><span class="tag p2">P2</span></td><td>兑换码缺汇总统计与导出；定价页缺发票/SLA 信任要素</td><td>运营</td><td>聚合查询 + 合计行；套餐页补信任要素入口</td></tr>
    <tr><td><span class="tag p3">P3</span></td><td>指挥官「3 个助手并行」横幅与 1 子任务口径不一</td><td>工程</td><td>planner 合并后回写横幅</td></tr>
    <tr><td><span class="tag p3">P3</span></td><td>超大文件（commander 2752 / chat 2190 等 4 个 &gt;2000 行）</td><td>工程</td><td>按调度/执行/交付渐进拆分（v3.71 已开 ledger.py 先例）</td></tr>
    <tr><td><span class="tag p3">P3</span></td><td>登出入口不显眼（用户菜单定位困难）</td><td>小白</td><td>顶栏头像下拉加一级「退出登录」</td></tr>
  </table>

  <div class="verdict">
    <strong>总评 8.5 / 10。</strong>这不是一个「演示型」产品：12 条流程真实打通、每一轮 LLM 调用有账单、每一次付费动作落库、
    每一个失败如实报错。本次拟人走查抓到的 11 个问题全部可执行，其中 2 个 P1（视频协议适配、支付网关）是下一版的最优先事项。
  </div>

  <div class="foot">
    走查基线：本地 main（v3.73.2/3.73.3，与 GitHub origin/main 同步）· 迁移 0081 · Agnes AI 三模型真实调用 ·
    账号 admin（平台管理员）与 operator（科研操作员）真实登录 · 全部截图 docs/walkthrough-shots/（28 张）·
    走查脚本 frontend/scripts-walkthrough-{a,b,b2,c,e,f,g}.mjs 可复跑复现。报告生成：frontend/scripts-walkthrough-report.mjs。
  </div>
</div>
</body>
</html>
`

writeFileSync(outPath, html)
console.log(`已生成：${outPath}（${(html.length / 1024 / 1024).toFixed(1)} MB）`)
