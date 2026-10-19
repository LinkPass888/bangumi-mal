# bangumi-mal

Bangumi（bgm.tv）ID → MyAnimeList ID 的合并映射表，每周由 GitHub Actions 自动重建。

用途：给（尤其是要喂给 **AniList** 的）应用做 ID 转换 —— 拿到一个 bangumi 条目 ID，查出对应的 MAL ID，
再用 AniList GraphQL 的 `idMal` 参数取条目：

```graphql
query ($mal: Int) { Media(idMal: $mal, type: ANIME) { id title { native romaji } } }
```

当前规模（见 [`data/meta.json`](data/meta.json)）：

| | 映射对数 | 说明 |
|---|---:|---|
| 合计 | **13,270** | 已去重的 bangumi → MAL |
| 两家一致 | 7,333 | |
| 仅 BangumiExtLinker | 4,991 | 老番、OVA、短片、国漫等 |
| 仅 bangumi-data | 946 | 主要是最新一两年新番 |
| 冲突（取值规则见下） | 77 | 全部记录在 `data/conflicts.json` |

## 数据来源与冲突规则

| 来源 | 角色 |
|---|---|
| [Rhilip/BangumiExtLinker](https://github.com/Rhilip/BangumiExtLinker) | **主表**，条目覆盖最广（23,206 条 bangumi 条目 / 12,324 条 MAL 映射） |
| [bangumi-data/bangumi-data](https://github.com/bangumi-data/bangumi-data) | **补缺**，新番更新最快 |

**两家给同一 bangumi ID 不同的 MAL ID 时，一律以 BangumiExtLinker 为准**，
被舍弃的 bangumi-data 值仍保留在 `data/conflicts.json` 中，便于人工复核。

## 接口（GitHub Pages，静态 JSON API）

基址 `https://linkpass888.github.io/bangumi-mal/`（仓库公开后可用）

| 文件 | 内容 | 大小 |
|---|---|---|
| `data/bangumi-mal.min.json` | `{"<bangumi_id>": <mal_id>}` 全量紧凑表 | ~200 KB |
| `data/bangumi-mal.json` | 同上，每行一条便于 diff | ~200 KB |
| `data/shards/<n>.json` | **按 `bangumi_id % 100` 分片的按需查询**，单片 ~2 KB | 100 个文件 |
| `data/bangumi-mal.records.json` | 完整记录（含标题、开播时间、来源标记） | ~2.8 MB |
| `data/bangumi-mal.csv` | 同上，CSV | ~1.2 MB |
| `data/mal-bangumi.json` | 反查 `{"<mal_id>": [<bangumi_id>, ...]}` | ~250 KB |
| `data/conflicts.json` | 77 条两家不一致的记录 | ~21 KB |
| `data/no-anilist.json` | AniList 查不到的 1,298 个 MAL id 名单（见下） | ~16 KB |
| `data/meta.json` | 生成时间 + 各项计数 | — |

### 用法

只要 MAL ID（推荐，AniList 那边直接按 `idMal` 查）：

```js
// 全量一次拉取，之后本地查表
const map = await (await fetch('https://linkpass888.github.io/bangumi-mal/data/bangumi-mal.min.json')).json();
const malId = map['8'];   // 2904
```

```js
// 只查一个 ID：只下 2 KB 的分片
const shard = await (await fetch(`https://linkpass888.github.io/bangumi-mal/data/shards/${8 % 100}.json`)).json();
const malId = shard['8']; // 2904
```

```python
import json, urllib.request
M = json.load(urllib.request.urlopen("https://linkpass888.github.io/bangumi-mal/data/bangumi-mal.min.json"))
mal_id = M.get("8")   # 2904
```

要标题 / 开播时间就拉 `bangumi-mal.records.json`：

```json
{"bangumi_id": 8, "mal_id": 2904, "title_native": "コードギアス 反逆のルルーシュR2",
 "title_cn": "Code Geass 反叛的鲁路修R2", "begin": "2008-04-06T08:00:00.000Z",
 "broadcast": "R/2008-04-06T08:00:00.000Z/P7D", "source": "both", "conflict": false}
```

`begin` 优先取 bangumi-data 的**完整时间戳**（ISO 8601，带时区）；只有 BangumiExtLinker 收录的条目会退化成
它的粗粒度日期（`"2024-10"` 或 `"2008-04-06"`），极少数两者都没有则为 `null`。
`broadcast` 是 bangumi-data 的 RFC 5545 重复规则（`R/起始时间/周期`，只有连载中的番才有），非该来源的条目为 `null`。

`source` 取值：`both`（两家一致）/ `extlinker` / `bangumi-data`（只有这家有）。
`conflict: true` 表示两家不一致、已按规则取 BangumiExtLinker 的值，原值在 `alternate_mal_id`。

## 有多少映射在 AniList 里查不到

有一部分 MAL id **真实存在于 MyAnimeList、但在 AniList 没有对应动画条目** —— AniList 基本不收录国产动画，
日本的总集篇 / 特别篇 / 短篇 OVA / 宣传片也大量缺失。2026-10-19 全量核对（13,080 个 MAL id 逐个查 AniList）：

| | 数量 |
|---|---:|
| 映射行总数 | 13,270 |
| 其中 AniList 查不到 | **1,305 行 / 1,298 个不同 MAL id**（9.9%） |
| —— 来自「仅 BangumiExtLinker」段 | 1,197 / 4,914（**24.4%**） |
| —— 来自「仅 bangumi-data」段 | 17 / 946（1.8%） |
| —— 来自「两家一致」段 | 78 / 7,333（1.1%） |

**这些不是错映射**：抽样 70 条直查 myanimelist.net，只有 2 条是真 404，其余都是标题正常的真实条目；
按年份分布 <2000 有 23%、2000s 14%、2010s 46%、2020+ 17%，说明是 AniList 的内容盲区而不是年代问题。

名单在 `data/no-anilist.json`（**快照，非每周生成**），客户端要按 AniList 取条目时可以提前跳过。
注意：ExtLinker 独有的 4,914 条里有近四分之一属于这类，对「喂 AniList」这个用途来说它的增量要打折。

## 自动更新

[`.github/workflows/update.yml`](.github/workflows/update.yml)：每周一 04:00 UTC（北京时间 12:00）拉取上游、
重建全部数据文件并提交（上游没有变化时，只有 `data/meta.json` 的生成时间会更新）。
也可以在 Actions 页面手动 `Run workflow`。上游所有地址都拉取失败时任务直接失败，不会写出空数据。

本地重建：

```sh
python3 build.py          # 用 .cache/ 里的上游快照
REFRESH=1 python3 build.py  # 强制重新下载上游
```

脚本零第三方依赖，只用标准库。

## 许可与署名

映射数据由上述两个上游项目整理，均以 **CC BY 4.0** 发布，本仓库的再分发同样遵循 CC BY 4.0，
使用时请保留对 BangumiExtLinker 与 bangumi-data 的署名。构建脚本以 MIT 许可发布。

由 [LinkPass888](https://github.com/LinkPass888) 维护。
