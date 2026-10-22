# 设计系统 v2「静夜玻璃」— 2026-10-22 UI 全面升级规格

> 本文件是 UI 升级的唯一事实源。所有实现必须引用 token，不得写魔法色值。
> 基调：深色、安静、玻璃质感、一处渐变（品牌动作），其余克制。

## 1. 色彩 token（styles/design-system.css :root）

```
--ds-bg-0: #0B0E14;        /* 页面底 */
--ds-bg-1: #11151F;        /* 面板底 */
--ds-glass: rgba(255,255,255,.045);      /* 玻璃填充 */
--ds-glass-strong: rgba(255,255,255,.07);/* hover 玻璃 */
--ds-border: rgba(255,255,255,.09);
--ds-border-strong: rgba(255,255,255,.16);
--ds-text-1: #E8EAF2;      /* 主文本 */
--ds-text-2: #A3AABF;      /* 次文本 */
--ds-text-3: #6C7386;      /* 弱化/占位 */
--ds-accent: #6366F1;      /* 品牌靛紫 */
--ds-accent-2: #22D3EE;    /* 青（渐变尾色/图表） */
--ds-success: #34D399; --ds-warning: #FBBF24; --ds-danger: #F87171;
--ds-grad-brand: linear-gradient(135deg,#6366F1 0%,#22D3EE 100%);
```

Element Plus 暗色桥接：`html.dark` + 覆盖 `--el-color-primary(-light-3/-dark-2)`、
`--el-bg-color*`、`--el-border-color*`、`--el-text-color*`、`--el-border-radius-base: 10px`
全部映射到 --ds-* 上。main.ts 引入 `element-plus/theme-chalk/dark/css-vars.css` 并给
html 加 dark 类（在 design-system 引入之前）。

## 2. 型制（一切圆角/间距/阴影只有这三档）

- 圆角：卡片/对话框 14px；控件（按钮/输入/标签）10px；胶囊（徽章）999px
- 控件高度：默认 34px、小号 28px（el 组件 size 统一 small 用于表格内、default 用于表单）
- 阴影：`--ds-shadow: 0 8px 32px rgba(0,0,0,.35)`；玻璃面板不加阴影靠边框发光 hover
- 字体：`-apple-system,'PingFang SC','Microsoft YaHei'`；页面标题 20/600（.page-title 升级：
  字距 -0.01em）；卡片头 15/600；正文 14；辅助 13；数字用 font-variant-numeric: tabular-nums

## 3. 玻璃面板配方（卡片统一长这样）

```
background: var(--ds-glass);
backdrop-filter: blur(18px) saturate(1.2);
border: 1px solid var(--ds-border);
border-radius: 14px;
transition: border-color .2s, background .2s, transform .2s;
hover(可点卡片): border-color: var(--ds-border-strong); background: var(--ds-glass-strong); translateY(-2px)
```
页面背景：`--ds-bg-0` 固定 + 两个 40vmax 径向渐变光斑（靛紫右上 8%、青左下 5%，fixed、pointer-events:none）。

## 4. 品牌动作（全站唯一渐变处）

主按钮 primary（el-button--primary 覆盖：渐变底、无边框、hover 提亮）、登录页主按钮、
侧栏激活条（2px 左竖条渐变）、logo 方块、空状态主 CTA。**除此之外禁止渐变**（图表色板可用
accent→accent-2 序列）。

## 5. 应用外壳（MainLayout 重造）

- 侧栏 232px（可折叠 64px）：顶部 logo（渐变方块 + 「阗策·科技小院」字标，折叠只留方块）
- 导航**分组小标**（大写字距 .08em 12px --ds-text-3）：
  「工作台」：概览/工作台/▣ 对话(新)/问答；「能力」：专业助手/协作班组/Agent市场/智能工厂；
  「数据」：知识库/设备接入/任务中心；「管理」：审核/成员/配额与观测/推送与设置/进化/全局总览
- 激活项：--ds-glass-strong 底 + 左 2px 渐变条 + 图标变 accent；hover 只加底不加边框
- 顶栏 56px：面包屑(组/页) + 右侧用户胶囊（头像+名+角色徽章，弹出菜单：退出）；去掉旧 header 的一切重边框
- 路由过渡：fade 120ms；内容区 max-width 1440 居中，padding 24（窄屏 12）

## 6. 组件协调律（全局 CSS 兜底 + 各页少改）

- 页面骨架统一：`.page-title`（左标题+右侧操作区 flex 两端对齐）+ `.page-actions` 按钮组 gap 8
- 表格：透明底、行 hover --ds-glass、表头 13/--ds-text-2/大写感；操作列文字按钮统一 link 型
- 标签 tag：胶囊 999px、12px、状态色板（成功 success、进行中 accent、警示 warning、失败 danger）
- 对话框/抽屉：玻璃底 14px 圆角、头部 15/600、底部按钮右对齐（取消在前）
- 表单：label 位置 top、组间距 18px、输入聚焦 ring = accent 30% 边框 + 3px 外发光
- 空状态：图标 + 一句话 + 主 CTA（渐变），文案口语（"还没有…，去创建一个"）
- 滚动条 8px 圆头 --ds-border-strong；选中文字 accent 底

## 7. 对话模式（ChatView，全新）

三段式：左侧会话列表 260px（玻璃列表项：标题+时间+悬浮删除，顶部「新对话」渐变钮）→
中部消息流（max-width 820 居中）→ 底部输入区（粘性、玻璃、textarea 自增高 ≤160px、
Enter 发送 Shift+Enter 换行、发送钮渐变圆钮；发送中变停止样式但仅禁用）。
- 用户气泡：accent 深底 14px 圆角右对齐；助手气泡：玻璃底左对齐，**顶部小徽章**
  「↳ 由 {choice.name} 回答 · {routed_by 徽章}」（rules=规则命中/success，llm=智能分类/accent，
  fallback=兜底/warning，tooltip=reason）——诚实标记延续平台口径
- 助手消息渲染：纯文本 pre-wrap（不引 markdown 库）；等待中三点波浪动画
- 顶部条：会话标题(可改由首条用户消息前 20 字自动生成) + 右侧「模型路由：自动」静态说明
- 空态：居中大标题「直接说要办的事」+ 四个建议卡（点击填入输入框）
- 流式：v1 同步等待 + 打字气泡；API 见 docs/DESIGN-v2.md 契约（后端同波交付）

## 8. 登录/注册页

全屏居中玻璃卡（420px），logo+字标在上，账号密码表单，卡下方小字「演示环境账号见 README」；
背景同页面光斑配方；提交按钮通栏渐变。

## 9. 硬约束

- 不引新依赖（无 tailwind/无图标库变更：继续 @element-plus/icons-vue）
- 业务逻辑/接口调用零改动；只动样式、模板结构微调、MainLayout、路由 meta
- 每页 script 逻辑保护：改模板必须保留全部既有绑定与事件
- 门禁：npm run lint 0 新 error + npm run build 通过

## 10. 对话模式后端契约（前后端共同遵守）

- `GET  /api/v1/chat/sessions` → `{items:[{id,title,target,target_code,updated_at,last_excerpt}]}`（当前用户自己的会话，新→旧）
- `POST /api/v1/chat/sessions` body `{title?, target?: 'auto'|'agent'|'team', target_code?}` → 会话对象
- `PATCH /api/v1/chat/sessions/{id}` body `{title}` → 会话对象
- `DELETE /api/v1/chat/sessions/{id}` → 204 语义（ok 包）
- `GET  /api/v1/chat/sessions/{id}/messages` → `{items:[{id,role:'user'|'assistant',content,choice?,routed_by?,reason?,created_at}]}`（升序）
- `POST /api/v1/chat/sessions/{id}/messages` body `{content}` → `{user_message, assistant_message}`
  - target=auto：走 supervisor 三级路由；assistant_message.choice = {kind,code?,name}、routed_by、reason
  - target=agent/team：直跑指定对象；routed_by='manual'
  - 执行失败：assistant_message.content = 中文错误说明，routed_by='error'（会话不死，可继续问）
- 全部登录可用（成员级），跨租户/跨用户会话一律 404
