# -*- coding: utf-8 -*-
"""哈希对比：重复分组、选中项判定、参考值校验、多算法交叉一致性。"""
from __future__ import annotations

from .util import norm_key, normalize_hash


def buckets_for(entries, algorithm: str) -> dict[str, list]:
    """``{哈希: [条目]}``，只统计该算法已算出结果的条目。"""
    out: dict[str, list] = {}
    for entry in entries:
        value = entry.hash_of(algorithm)
        if value:
            out.setdefault(value.upper(), []).append(entry)
    return out


def duplicate_groups(entries, algorithm: str) -> list[list]:
    """出现两次以上的分组，按「文件数从多到少」排序。"""
    groups = [g for g in buckets_for(entries, algorithm).values() if len(g) > 1]
    groups.sort(key=lambda g: (-len(g), g[0].path))
    return groups


def pick_primary(entries, algorithms) -> str:
    """选主算法：优先选算出结果最多的那个，一样多则按界面顺序取前面的。"""
    best, best_count = "", -1
    for alg in algorithms or []:
        count = sum(1 for e in entries if e.hash_of(alg))
        if count > best_count:
            best, best_count = alg, count
    return best


def analyse(entries, algorithms) -> dict:
    """汇总一次分析结果，供界面着色与状态栏使用。

    返回 ``{"primary", "labels": {path: 文本}, "duplicates": set,
    "group_count", "conflicts": set}``。

    ``conflicts`` 是「在不同算法下分组结论不一致」的文件：
    例如两文件 SHA-256 相同但 MD5 不同，说明数据被动过或算错了，
    这正是多算法对比最有价值的地方。
    """
    primary = pick_primary(entries, algorithms)
    labels: dict[str, str] = {}
    duplicates: set[str] = set()
    group_no = 0
    if primary:
        for index, group in enumerate(duplicate_groups(entries, primary), 1):
            group_no = index
            for entry in group:
                labels[entry.path] = f"组 {index} · {len(group)} 个相同"
                duplicates.add(entry.path)

    conflicts: set[str] = set()
    usable = [a for a in (algorithms or []) if any(e.hash_of(a) for e in entries)]
    if len(usable) > 1 and primary:
        # 同一算法分组内，检查其它算法是否也一致
        for group in duplicate_groups(entries, primary):
            for other in usable:
                if other == primary:
                    continue
                if len({e.hash_of(other).upper() for e in group if e.hash_of(other)}) > 1:
                    conflicts.update(e.path for e in group)
    return {
        "primary": primary,
        "labels": labels,
        "duplicates": duplicates,
        "group_count": group_no,
        "conflicts": conflicts,
    }


def compare_paths(entries, algorithms, paths) -> str:
    """给出「这几个文件是否相同」的人话结论。

    ``Entry.path`` 存的是归一化键（Windows 下小写），所以传进来的路径
    也要先归一化，否则匹配不上、结论永远是「选中不足 2 个」。
    """
    wanted = {norm_key(p) for p in paths}
    picked = [e for e in entries if e.path in wanted]
    if len(picked) < 2:
        return "选中 2 个及以上文件即可对比"
    lines = []
    usable = [a for a in (algorithms or []) if all(e.hash_of(a) for e in picked)]
    if not usable:
        return "选中的文件还有未计算出的哈希，请先算完再对比"
    same_everywhere = True
    for alg in usable:
        values = {e.hash_of(alg).upper() for e in picked}
        if len(values) == 1:
            lines.append(f"{alg}：{len(picked)} 个文件完全相同")
        else:
            same_everywhere = False
            lines.append(f"{alg}：{len(picked)} 个文件里有 {len(values)} 种不同值")
    head = "结论：内容一致 ✅" if same_everywhere else "结论：存在差异 ❌"
    return head + "\n" + "\n".join(lines)


REFERENCE_MIN_CHARS = 4
_HIT_RANK = {"exact": 3, "prefix": 2, "contains": 1}


def reference_hit(hash_value: str, query: str) -> str:
    """单个哈希对参考值的命中强度：``exact`` / ``prefix`` / ``contains`` / ``""``。

    只支持完整值比对时，从网页上复制的半截哈希、或者只记得开头几位的情况
    就完全用不上；所以短查询按「前缀优先、其次中间包含」来匹配。
    少于 :data:`REFERENCE_MIN_CHARS` 位不匹配 —— 3 个十六进制字符在 64 位
    哈希里命中是大概率事件，标出来只会误导。
    """
    value = (hash_value or "").upper()
    needle = normalize_hash(query).upper()
    if not value or len(needle) < REFERENCE_MIN_CHARS:
        return ""
    if needle == value:
        return "exact"
    if len(needle) > len(value):
        return ""
    if value.startswith(needle):
        return "prefix"
    if needle in value:
        return "contains"
    return ""


def reference_hits(entries, algorithms, reference: str) -> dict[str, tuple[str, str]]:
    """返回 ``{路径: (命中级别, 命中的算法)}``，同一行取最强的一次命中。"""
    needle = normalize_hash(reference)
    if len(needle) < REFERENCE_MIN_CHARS:
        return {}
    out: dict[str, tuple[str, str]] = {}
    for entry in entries:
        best_level, best_alg = "", ""
        for alg in algorithms or []:
            level = reference_hit(entry.hash_of(alg), needle)
            if level and (not best_level or _HIT_RANK[level] > _HIT_RANK[best_level]):
                best_level, best_alg = level, alg
        if best_level:
            out[entry.path] = (best_level, best_alg)
    return out


def reference_matches(entries, algorithm: str, reference: str) -> set[str]:
    """哈希值与参考值完全一致的文件路径集合。"""
    return {path for path, (level, _alg) in
            reference_hits(entries, [algorithm], reference).items() if level == "exact"}
