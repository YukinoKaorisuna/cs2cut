# FACEIT 零门槛抓取链路 —— 实测验证结果

验证时间：2026-10-01
验证对象：donk666（FACEIT 全球第 1，5055 Elo）

## 核心突破：不需要 API Key，不需要登录

FACEIT 网页前端自己用的内部接口**完全公开可访问**。
之前折腾的「申请 API Key → 2FA → 身份验证」**全部不需要了**。

### 验证过的 4 个接口（全部 200 OK）

| 用途 | 接口 | 状态 |
|---|---|---|
| 昵称 → 用户档案 | `GET https://www.faceit.com/api/users/v1/nicknames/{nickname}` | ✅ 200 |
| 比赛历史（含每场击杀） | `GET https://www.faceit.com/api/stats/v1/stats/time/users/{player_id}/games/cs2?size=20&page=0` | ✅ 200 |
| 比赛详情（含 demo 直链） | `GET https://www.faceit.com/api/match/v2/match/{match_id}` | ✅ 200 |
| demo 文件 | `https://demos-europe-central.backblaze.faceit-cdn.net/cs2/{match_id}-1-1.dem.zst` | ⚠️ 代理问题 |

备用域名：`api.faceit.com` 与 `www.faceit.com/api` **等价可用**。

请求头要求（宽松）：
```
User-Agent: Mozilla/5.0 ... Chrome/131.0.0.0 Safari/537.36
Accept: application/json
Referer: https://www.faceit.com/
```

## 关键：donk666 的真实 ID

```
nickname: donk666
player_id: e5e8e2a6-d716-4493-b949-e16965f41654
country: kr
```

⚠️ **注意**：FACEIT 上有多个叫 `donk666` 的账号（搜索显示有 911 Elo 的、1000 Elo 的假号）。
**真身靠 Elo 判断 —— 5055 Elo 那个才是。** 抓取时必须校验 Elo 或 player_id。

## FACEIT 编码字段对照表（重要）

### ⚠️ 先记住：两套接口的字段含义不同！

| 接口 | `i14` 的含义 |
|---|---|
| 比赛历史 `/stats/time/users/...` | 结果码（3=胜） |
| 比赛统计 `/stats/matches/{id}` | **3K（triples）** |

混用两者是踩过的最大的坑。

### 比赛历史接口（`/stats/time/users/...`）

| 字段 | 含义 | 示例 |
|---|---|---|
| `i6` | **击杀 Kills** | 21 |
| `i8` | **死亡 Deaths** | 17 |
| `i9` | **助攻 Assists** | 4 |
| `i12` | 回合数 Rounds | 24 |
| `c2` | **KD 比** | 1.24 |
| `c4` | **ADR** | 76 |
| `c10` | **爆头率 HS%** | 85 |
| `i13` | 得分 Score | 16 |
| `i14` | 结果码（3=胜 2=负） | 3 |
| `i15` | 平局数 | 0 |
| `i18` | **比分** | 13 / 11 |
| `i1` | **地图** | de_ancient |
| `i0` | 地区 | EU |
| `i40` | 连胜/连败 | 3 |
| `i20` | 用枪统计 | 2041 |
| `elo` | 当前 Elo | 5053 |
| `elo_delta` | Elo 变化 | -30 |
| `matchId` | 完整比赛 ID | 1-17dcbb48-2142-45bd-be11-ecadbddf52ec |
| `created_at` | 毫秒时间戳 | 1790620257960 |

### 比赛统计接口（`/stats/matches/{match_id}`）—— 多杀在这里

返回数组，`arr[0]["teams"][*]["players"][*]`，按 `playerId` 匹配目标玩家：

| 字段 | 含义 |
|---|---|
| `i6` | 击杀 |
| `i7` | 助攻 |
| `i8` | 死亡 |
| `i9` | MVP |
| `i1` | 地图 |
| `i12` | 回合数 |
| `i18` | 比分 |
| **`i14`** | **3K（triples）** |
| **`i15`** | **4K（quadras）** |
| **`i16`** | **5K / ACE（aces）** |

**结论：3K/4K/5K 不需要下载 demo，接口直接给。** 但只给整场次数，
**不给「第几回合拿的」**——那个仍需 demo 逐回合解析。

## demo 直链格式

```
https://demos-europe-central.backblaze.faceit-cdn.net/cs2/
  {match_id}-1-1.dem.zst
```

- 比赛详情 JSON 里字段名是 **`demoURLs`**（数组，驼峰）
- 格式：`.dem.zst`（zstd 压缩）
- 区域节点会变化（如 `demos-europe-central`、`demos-europe-west`）
- **官方标注有效期 30 天**

## ⚠️ 当前唯一阻塞：本机代理

实测现象：
- 走代理（`127.0.0.1:52047`）：CDN 域名 **502 Bad Gateway**
- 不走代理（`trust_env=False`）：**DNS 解析失败**（本机无直连外网能力）

结论：**需要把 `*.backblaze.faceit-cdn.net` 加入代理白名单/规则**。
这不是代码或权限问题。

## FACEIT 天梯 vs 职业赛

- 职业选手确实在 FACEIT 打天梯，但**用带后缀的昵称**（donk666 而非 donk）
- FACEIT 排行榜可直接确认身份：donk666 (5055)、dem0nnnnnnnn (4870)、nipl (4729)...
- 排行榜地址：`https://www.faceit.com/en`（首页底部）

## 已推翻的旧结论

之前判断「必须申请 API Key（含 2FA + 身份验证）才能用」是**错的**。
那是走 `open.faceit.com/data/v4` 官方 Data API 的要求。
**内部接口完全不需要。** 但内部接口无文档、可能随时变更，需做好容错。
