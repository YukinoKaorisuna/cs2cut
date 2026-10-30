# FACEIT 深度调研笔记

调研时间：2026-09-30

## 一、数据获取能力分级

| 能力 | 接口 | 是否需要审批 | 现状 |
|---|---|---|---|
| 查选手（昵称→ID） | `GET /players?nickname=` | 免费 API Key | ✅ 可用 |
| 生涯统计 | `GET /players/{id}/stats/{game}` | 免费 API Key | ✅ 可用 |
| 比赛历史 | `GET /players/{id}/history` | 免费 API Key | ✅ 可用 |
| 单场统计（每人 K/D/ADR/HS%） | `GET /matches/{id}/stats` | 免费 API Key | ✅ 可用 |
| 比赛详情（含 demo_url） | `GET /matches/{id}` | 免费 API Key | ✅ 可用 |
| **demo 换 signed URL** | `POST /download/v2/demos/download` | **专项审批（约 30 天）** | ⚠️ 受限 |
| 逐回合多杀（3K/4K/5K） | 无此接口 | — | ❌ 必须解析 demo |

Base URL：`https://open.faceit.com/data/v4`
认证：`Authorization: Bearer <API_KEY>`
申请：https://developers.faceit.com → App Studio → 新建 App → server-side API Key

## 二、demo 下载的关键变化（2025 年起）

FACEIT 把 demo 下载从公开改为**私有 API**：

- demo 文件存放于私有 S3，**必须 signed URL 才能访问**
- 换 signed URL 需要 **带 Downloads scope 的独立 Access Token**
- 申请地址：https://fce.gg/downloads-api-application
- 官方标注**响应时间 30 天**，咨询邮箱 partnerships@faceit.com
- 申请内容分 3 部分，需要说明用途
- 订阅 `match_demo_ready` webhook 可在 demo 上传完成时收到通知

**旁证**：CS Demo Manager（业内最主流的 demo 管理器）因这一变化，
已关闭 FACEIT 应用内下载功能，改为「跳转浏览器手动点 Watch Demo」，
并明确说明「需要自建后端服务来维持下载，恢复时间未知」。

## 三、cookie 会话路线（备选）

社区做法（如 sparkoo/csgo-2d-demo-viewer 浏览器扩展）：
1. 用 `credentials: 'include'` 带上 FACEIT 会话 cookie
2. 调 match API 拿 demo 资源 URL
3. 调下载接口换取 signed URL

- 优点：即时可用，无需审批
- 缺点：**违反 FACEIT ToS**；cookie 会过期需重登；有 Cloudflare 风控；高频请求被封

结论：仅适合个人小规模本地使用。

## 四、技术选型

- **demo 解析**：`demoparser2`（Rust 实现，Python 绑定，性能好）
  - 用 `parse_event("player_death")` 拿击杀事件
  - 按 `(回合号, attacker_steamid)` 分组计数 → ≥3 即多杀
  - 字段：`tick`、`attacker_steamid`、`attacker_name`、`user_name`、`weapon`、`headshot`
- **demo 压缩**：CS2 用 zstd（`.dem.zst`），解析前需解压（`zstandard` 库）
- **demo 有效期**：官方明确 **30 天**，只能抓近期场次

## 五、选手昵称注意事项

FACEIT 昵称大小写敏感，且**和职业 ID 不一定一致**。例如：
- ZywOo 在 FACEIT 上的昵称可能就是 `ZywOo`
- NiKo / donk / s1mple 需要实际查询确认
- 有些职业选手不在 FACEIT 活跃，或使用小号

建议先用工具查一遍确认能查到，再批量采集。

## 六、合规红线

- 官方 Data API 只抓公开数据，合规
- cookie 路线违反 ToS，风险自负
- 不建议大规模抓取（rate limit + 法律风险）
- demo 版权归 FACEIT / 选手，用于商业用途需谨慎
