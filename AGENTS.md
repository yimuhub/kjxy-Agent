# AGENTS.md — 界面与交互强制规范

本文件是前端改动的硬约束。新增或修改页面、弹窗、表格、表单前必须先读；违反规范的改动不得合并。

## 1. 弹窗：一律居中

- 所有表单、详情、确认类弹窗必须使用 `el-dialog`，并带 `append-to-body`；禁止用右侧 `el-drawer` 承载业务表单。
- 居中由 `frontend/src/styles/design-system.css` 的 `.el-overlay-dialog > .el-dialog` 统一保证；新增弹窗不得覆盖该布局。
- 宽度使用响应式写法，例如 `width="min(1180px, 96vw)"`；内容放不下时表格/表单内部横向滚动，**禁止压缩到文字重叠**。
- 标题、正文、底部操作区必须留白完整；长文本用 `show-overflow-tooltip` 或换行，不允许「字压字」。
- 需要移动时使用 `draggable`，不要用固定 `right/top` 定位模拟居中。

示例：

```vue
<el-dialog
  v-model="visible"
  append-to-body
  align-center
  draggable
  width="min(760px, 94vw)"
  title="编辑"
>
  ...
</el-dialog>
```

## 2. 表格：多列表格必须可拖动列宽

- 所有 `el-table`（含两列及以上的表格）必须加全局指令 `v-col-resize`。
- 指令提供：表头列边界拖动调宽、最小 80px、双击恢复默认、按页面/列名持久化到 `localStorage`。
- 表格容器必须有横向滚动（如 `overflow-x: auto`），不能用固定窄列硬挤导致文字叠在一起。
- 操作列靠右固定，宽度不小于 200px；状态/标签列不小于 140px；单元格与表头默认 `white-space: nowrap`。
- 新增表格若未挂 `v-col-resize`，`scripts/check_ui_standards.py` 会直接失败。

示例：

```vue
<el-table
  v-col-resize
  :data="rows"
  border
>
  <el-table-column prop="name" label="名称" min-width="220" />
</el-table>
```

## 3. 表单：价格、用量等字段一律选填

- 单价（`price_input` / `price_output` / `pricing.unit`）、上下文窗口、最大输出等均为**选填**。
- 未填写时提交 `null` 或不提交，禁止用 `0` 伪装成已配置；空值不得触发后端校验错误。
- 后端 `pricing.unit`、`pricing.currency` 是字符串元数据，只有数值字段要求 `>= 0`；`None`、空字符串、`{}` 等价于未配置。
- 表单标签或提示必须明确写「选填」。

## 4. 模型模态：自动识别优先，手动兜底

模型新增/编辑弹窗必须按以下顺序识别模态，不得默认静默当作文本：

1. 本地模型名/上游 ID 关键字规则；
2. 用供应商已存密钥拉取上游 `/models`，读取 `modality`、`type`、`architecture.input_modalities/output_modalities`、`capabilities` 等元数据；
3. 仍无法识别时，提示「未能自动识别，请手动选择模态」，由用户选择后保存。

识别结果显示来源（模型名 / 上游目录），并允许「自动识别」按钮重试；不得阻塞保存。

## 5. 模型选择：供应商 → 模型级联，按模态过滤

任何「选模型」的入口（全局默认、按能力、按助手、助手专属能力、视觉能力、助手编辑页）必须使用统一的 `SupplierModelSelect`，不得再出现「先选供应商、再手填模型名」的旧控件：

- 一个下拉里按供应商分组列模型，**只列已启用供应商 + 已启用且模态匹配的模型**；供应商停用后，其模型不得出现在候选里。
- 模态按能力决定（`frontend/src/utils/modelPurpose.ts`）：图像能力只列 `image`，视频只列 `video`；文本类能力列 `text`/`multimodal`；视觉能力不得混入对话模型。
- 允许只绑供应商时传 `allow-default`，选项文案带供应商名（如「某供应商 · 使用默认模型」），避免跨供应商同名模型分不清。
- 绑定值优先写 `supplier_model_id`；后端解析仍兼容旧的 `model_name`/供应商默认模型，新增功能不得绕过模型行。
- 后端保存视觉能力绑定时同样校验模态/协议（`_ensure_visual_suppliers`），前端过滤只是体验优化，不能替代后端校验。

## 6. 自检命令

提交前必须通过：

```bash
python scripts/check_versions.py
python scripts/check_ui_standards.py
make check
```

`check_versions.py` 要求 9 处版本面与 `pyproject.toml` 一致，规则见根目录 `VERSIONING.md`；
`check_ui_standards.py` 检查：不得出现 `el-drawer`、所有 `el-dialog` 必须 `append-to-body`、所有多列表格必须 `v-col-resize`。
