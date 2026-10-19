#!/usr/bin/env python3
"""
bangumi-mal : build a merged Bangumi ID -> MyAnimeList ID mapping table.

Sources
  1. Rhilip/BangumiExtLinker  data/anime_map.json   (primary, wins every conflict)
  2. bangumi-data/bangumi-data dist/data.json       (fills gaps / brand new titles)

Outputs (./data)
  bangumi-mal.json           compact lookup  {"<bangumi_id>": <mal_id>, ...}
  bangumi-mal.min.json       same, no indentation (smaller transfer)
  bangumi-mal.records.json   array of full records
  bangumi-mal.csv            bangumi_id,mal_id,title_native,title_cn,begin,source,conflict
  mal-bangumi.json           reverse index  {"<mal_id>": [<bangumi_id>, ...]}
  conflicts.json             every disagreement between the two sources
  meta.json                  counts + source freshness

No third-party dependencies on purpose (runs on a bare GH runner).
"""
from __future__ import annotations

import csv
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

BX_URLS = [
    "https://raw.githubusercontent.com/Rhilip/BangumiExtLinker/main/data/anime_map.json",
    "https://rhilip.github.io/BangumiExtLinker/data/anime_map.json",
    "https://raw.githubusercontent.com/Rhilip/BangumiExtLinker/master/data/anime_map.json",
]
BD_URLS = [
    "https://raw.githubusercontent.com/bangumi-data/bangumi-data/master/dist/data.json",
    "https://cdn.jsdelivr.net/gh/bangumi-data/bangumi-data@master/dist/data.json",
]

OUT_DIR = os.environ.get("OUT_DIR", "data")
REFRESH = os.environ.get("REFRESH") == "1"


def fetch(urls: list[str], cache: str) -> dict:
    """Download the first URL that works; fall back to the cache file."""
    if not REFRESH and os.path.exists(cache):
        try:
            with open(cache, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            pass
    ctx = ssl.create_default_context()
    last = None
    for url in urls:
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "bangumi-mal-builder/1.0"})
                with urllib.request.urlopen(req, context=ctx, timeout=120) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                print(f"  ok  {url} ({len(json.dumps(data)) // 1024} KB json)")
                return data
            except Exception as exc:  # noqa: BLE001
                last = exc
                print(f"  fail {url} attempt {attempt + 1}: {exc}")
    if os.path.exists(cache):
        print(f"  !! all downloads failed, reusing cache {cache}: {last}")
        with open(cache, "r", encoding="utf-8") as fh:
            return json.load(fh)
    raise SystemExit(f"could not fetch any source: {last}")


def norm_id(value) -> int | None:
    if value is None:
        return None
    s = str(value).strip()
    if not re.fullmatch(r"\d+", s):
        return None
    n = int(s)
    return n if n > 0 else None


def load_extlinker(raw: list) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for row in raw:
        bgm = norm_id(row.get("bgm_id"))
        mal = norm_id(row.get("mal_id"))
        if bgm is None:
            continue
        out[bgm] = {
            "mal_id": mal,
            "title_native": (row.get("name") or "").strip() or None,
            "title_cn": (row.get("name_cn") or "").strip() or None,
            "begin": (row.get("date") or "").strip() or None,
        }
    return out


def load_bangumi_data(raw: dict) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for item in raw.get("items", []):
        sites = {}
        for s in item.get("sites", []):
            sites[s.get("site")] = s.get("id")
        bgm = norm_id(sites.get("bangumi"))
        if bgm is None:
            continue
        zh = (item.get("titleTranslate") or {}).get("zh-Hans") or []
        begin = (item.get("begin") or "")[:7] or None
        out[bgm] = {
            "mal_id": norm_id(sites.get("mal")),
            "title_native": (item.get("title") or "").strip() or None,
            "title_cn": (zh[0] or "").strip() if zh else None,
            "begin": begin,
        }
    return out


def main() -> None:
    print("fetching BangumiExtLinker ...")
    bx = load_extlinker(fetch(BX_URLS, ".cache/extlinker.json"))
    print(f"  entries: {len(bx)}  with mal: {sum(1 for v in bx.values() if v['mal_id'])}")
    print("fetching bangumi-data ...")
    bd = load_bangumi_data(fetch(BD_URLS, ".cache/bangumi-data.json"))
    print(f"  entries: {len(bd)}  with mal: {sum(1 for v in bd.values() if v['mal_id'])}")

    records: dict[int, dict] = {}
    conflicts: list[dict] = []

    # 1. BangumiExtLinker is the backbone
    for bgm, v in bx.items():
        if v["mal_id"] is None:
            continue
        rec = {
            "bangumi_id": bgm,
            "mal_id": v["mal_id"],
            "title_native": v["title_native"],
            "title_cn": v["title_cn"],
            "begin": v["begin"],
            "source": "extlinker",
            "conflict": False,
        }
        other = bd.get(bgm)
        if other:
            if (other.get("title_native") and not rec["title_native"]):
                rec["title_native"] = other["title_native"]
            if (other.get("title_cn") and not rec["title_cn"]):
                rec["title_cn"] = other["title_cn"]
            if (other.get("begin") and not rec["begin"]):
                rec["begin"] = other["begin"]
            if other["mal_id"] == rec["mal_id"]:
                rec["source"] = "both"
            elif other["mal_id"] is not None:
                # disagreement -> ExtLinker wins, keep the other value for auditing
                rec["conflict"] = True
                rec["alternate_mal_id"] = other["mal_id"]
                conflicts.append({
                    "bangumi_id": bgm,
                    "title_native": rec["title_native"],
                    "title_cn": rec["title_cn"],
                    "begin": rec["begin"],
                    "chosen": {"mal_id": rec["mal_id"], "source": "BangumiExtLinker"},
                    "rejected": {"mal_id": other["mal_id"], "source": "bangumi-data"},
                })
        records[bgm] = rec

    # 2. bangumi-data fills the gaps (mostly the newest cours)
    for bgm, v in bd.items():
        if bgm in records or v["mal_id"] is None:
            continue
        records[bgm] = {
            "bangumi_id": bgm,
            "mal_id": v["mal_id"],
            "title_native": v["title_native"],
            "title_cn": v["title_cn"],
            "begin": v["begin"],
            "source": "bangumi-data",
            "conflict": False,
        }

    rows = [records[k] for k in sorted(records)]
    conflicts.sort(key=lambda c: c["bangumi_id"])

    lookup = {str(r["bangumi_id"]): r["mal_id"] for r in rows}
    reverse: dict[str, list[int]] = {}
    for r in rows:
        reverse.setdefault(str(r["mal_id"]), []).append(r["bangumi_id"])

    os.makedirs(OUT_DIR, exist_ok=True)

    def dump(name: str, obj, indent=None) -> None:
        path = os.path.join(OUT_DIR, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=indent, sort_keys=indent is not None)
            fh.write("\n")
        print(f"  wrote {path} ({os.path.getsize(path) // 1024} KB)")

    dump("bangumi-mal.json", lookup, 0)
    dump("bangumi-mal.min.json", lookup)
    dump("bangumi-mal.records.json", rows, 0)
    dump("mal-bangumi.json", reverse, 0)
    dump("conflicts.json", conflicts, 1)

    # static "random access" API: 100 shards keyed by bangumi_id % 100
    shard_dir = os.path.join(OUT_DIR, "shards")
    os.makedirs(shard_dir, exist_ok=True)
    shards: dict[int, dict[str, int]] = {}
    for r in rows:
        shards.setdefault(r["bangumi_id"] % 100, {})[str(r["bangumi_id"])] = r["mal_id"]
    for n in range(100):
        path = os.path.join(shard_dir, f"{n}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(shards.get(n, {}), fh, ensure_ascii=False)
            fh.write("\n")
    print(f"  wrote {shard_dir}/*.json (100 shards)")

    csv_path = os.path.join(OUT_DIR, "bangumi-mal.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["bangumi_id", "mal_id", "title_native", "title_cn", "begin", "source", "conflict"])
        for r in rows:
            w.writerow([r["bangumi_id"], r["mal_id"], r["title_native"] or "", r["title_cn"] or "",
                        r["begin"] or "", r["source"], "1" if r["conflict"] else ""])
    print(f"  wrote {csv_path} ({os.path.getsize(csv_path) // 1024} KB)")

    meta = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pairs": len(rows),
        "conflicts": len(conflicts),
        "by_source": {
            "extlinker": sum(1 for r in rows if r["source"] == "extlinker"),
            "bangumi-data": sum(1 for r in rows if r["source"] == "bangumi-data"),
            "both": sum(1 for r in rows if r["source"] == "both"),
        },
        "sources": {
            "BangumiExtLinker": "https://github.com/Rhilip/BangumiExtLinker",
            "bangumi-data": "https://github.com/bangumi-data/bangumi-data",
        },
        "conflict_policy": "BangumiExtLinker wins; the rejected MAL id is kept in conflicts.json",
        "endpoints": {
            "full": "bangumi-mal.min.json",
            "sharded": "shards/<bangumi_id % 100>.json"
        },
    }
    dump("meta.json", meta, 1)
    print(f"\ndone: {len(rows)} pairs, {len(conflicts)} conflicts")


if __name__ == "__main__":
    main()
