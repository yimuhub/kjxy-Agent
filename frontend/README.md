# 科技小院前端（kejixiaoyuan-frontend）

Vue 3 + TypeScript + Vite + Element Plus + Vue Router + Pinia + axios。

> TASK-006：Vue 前端基础与首版核心页面。

## 快速启动

```bash
cd frontend
npm install
npm run dev        # 开发，http://localhost:5173，/api 代理到 http://localhost:8000
```

## 构建与检查

```bash
npm run type-check  # npx vue-tsc --noEmit
npm run lint        # eslint
npm run build       # 类型检查 + vite build，产物在 dist/
npm run preview     # 预览构建产物
```

## 后端联调

- 唯一 API 前缀 `/api/v1`，开发时代理见 `vite.config.ts`（`/api -> http://localhost:8000`）。
- 生产部署如需改后端地址，设置环境变量 `VITE_API_BASE`（仅 `VITE_` 开头变量会被打包进前端，**绝不能放密钥**）。
- 每次请求的 `X-Request-ID` 由前端自动生成；`Authorization: Bearer <token>` 自动携带；401 自动清 token 并跳转登录页。
- 错误提示统一取后端错误信封 `error.message`（见 `contracts/error-codes.md`）。

## 后端未就绪时的演示（Mock）

```bash
# .env.local
VITE_USE_MOCK=true
npm run dev
```

`src/api/mock.ts` 提供固定 fixture（登录、工作台、问答、知识库、任务、审核、成员、设置全链路可演示）。**默认关闭**，注释与开关说明见该文件头。

## API 类型同步说明

- 唯一契约源：`contracts/openapi.yaml`（+ `contracts/json-schema/`、`contracts/examples/`、`contracts/error-codes.md`）。
- 前端类型手写在 `src/types/api.ts`，文件头标注了同步要求；**契约变更时必须同步更新该文件**，前端不得自行增删字段。
- 契约中仅声明为 `object`、未细化字段的响应体，在类型文件中标注为"约定字段"并全部可选，后端实现后以契约为准收紧。
- API 调用按资源分组在 `src/api/modules.ts`（auth/tenant/task/knowledge/qa/feedback/skill/llm/tool/audit/admin）。

## 目录结构

```
src/
  api/        client.ts（axios 实例/拦截器）、modules.ts（按资源分组的 API）、mock.ts（演示 fixture）
  components/ AsyncState.vue（loading/空/失败重试/无权限四态）
  layouts/    MainLayout.vue（侧边导航 + 顶栏）
  router/     index.ts（路由守卫）
  stores/     auth.ts（token/user/tenant）
  types/      api.ts（与契约同步的类型）
  utils/      status.ts（任务状态彩色 tag、无权限分类）
  views/      Login/Dashboard/QaView/KnowledgeView/TasksView/ReviewView/MembersView/SettingsView
```
