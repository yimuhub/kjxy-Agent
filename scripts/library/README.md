# 官方技能库/插件库发布 CLI

离线发布官方库：构建 `.kjxpack`、生成签名清单、发布前自检。
运行依赖仓库根 `.venv`（`cryptography` + 后端包），不需要数据库/网络。

## 0. 生成发布者密钥（一次性）

```bash
.venv/bin/python scripts/library/sign.py keygen --out publisher.seed
```

只打印公钥；私钥在 `publisher.seed`（请离线备份，勿进仓库）。
后续命令也可用环境变量 `LIBRARY_SIGNING_SEED`（base64url 32 字节）代替 `--key-file`。

## 1. 构建包

技能源目录需含 `definition.json`（SkillSpec 严格形态）；
插件源目录需含 `plugin.json`（声明式 manifest，`entry_point` 必须为空）。

```bash
.venv/bin/python scripts/library/build.py \
  --kind skill --code demo_skill --version 1.0.0 \
  --source ./demo_skill --out ./packages --key-file publisher.seed
```

输出 `packages/demo_skill-1.0.0.kjxpack` 与整包 sha256。包内固定四件：
`manifest.json`、`checksums.json`、`SIGNATURE`、`definition.json`（插件为 `plugin.json`）。

## 2. 生成清单

```bash
.venv/bin/python scripts/library/publish.py \
  --packages-dir ./packages --base-url https://library.example.com/stable/ \
  --channel stable --expires-days 30 --key-file publisher.seed
```

- `sequence` 未显式指定时读取既有 `manifest.json` 并 +1（服务端按单调序防回滚）；
- `--base-url` 用于拼 `download_url`，必须是 https；
- 撤销条目：`--removed official:skill:old_skill`（逗号分隔）；
- `manifest.json` 含 `schema_version/channel/sequence/generated_at/expires_at/publisher/items/signature`。

## 3. 发布前自检

```bash
.venv/bin/python scripts/library/verify.py \
  --manifest ./packages/manifest.json --packages-dir ./packages \
  --public-key "<keygen 输出的 public_key_b64>"
```

检查签名、schema/通道/序号/过期时间、整包 sha256、包内 checksums 与
SkillSpec/声明式插件规则；有错退出码为 1，可直接接 CI。

## 4. 在平台登记源

`POST /api/v1/library/sources`（平台管理员建官方源），
`public_key_b64` 填 keygen 打印的公钥（可另存为文件后用 `--public-key-file`）：

```json
{
  "name": "官方技能库",
  "kind": "official",
  "base_url": "https://library.example.com/stable/",
  "channel": "stable",
  "public_key_b64": "<keygen 输出的 public_key_b64>"
}
```

随后 `POST /api/v1/library/sources/{id}/sync` 或等待启动/定时同步；
离线环境下不配置源时，平台完全按本地已安装能力可用。
