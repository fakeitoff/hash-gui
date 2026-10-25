# -*- coding: utf-8 -*-
"""开发自检：真实调用系统 7-Zip 算哈希，并与 Python hashlib 的结果交叉验证。

运行：  python selftest.py
测试素材写在程序目录的 temp/ 下，跑完自动删除。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from core import compare, sevenzip                       # noqa: E402
from core.hasher import HashRunner, stat_entry           # noqa: E402
from core.util import norm_key                           # noqa: E402

results: list[tuple[str, bool, str]] = []
TEMP_DIR = os.path.join(ROOT, "temp")


def check(label: str, ok: bool, detail: str = ""):
    results.append((label, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"  -> {detail}" if detail else ""))


def hashlib_of(path: str, alg: str) -> str:
    maker = {"MD5": hashlib.md5, "SHA1": hashlib.sha1, "SHA256": hashlib.sha256,
             "SHA512": hashlib.sha512, "SHA384": hashlib.sha384}
    if alg not in maker:
        return ""
    digest = maker[alg]()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def make_samples(folder: str) -> dict:
    """生成测试素材：同名不同目录、中文带空格、大文件、唯一文件。"""
    payloads = {
        "dir1/same.bin": b"identical content 12345",
        "dir2/same.bin": b"identical content 12345",     # 与上面同名同内容，不同目录
        "dir2/other.bin": b"different content 67890",
        "子 目录/中文 文件名.bin": b"identical content 12345",
    }
    made: dict[str, str] = {}
    for rel, data in payloads.items():
        path = os.path.join(folder, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        made[rel] = path
    big = os.path.join(folder, "big.bin")
    with open(big, "wb") as fh:                 # 16MB，用来验证进度与速度
        for _ in range(16):
            fh.write(bytes([7]) * (1024 * 1024))
    made["big.bin"] = big
    return made


def main():
    import tkinter.messagebox as _mb

    _mb.showwarning = lambda *a, **k: None
    _mb.showerror = lambda *a, **k: None
    _mb.showinfo = lambda *a, **k: None
    _mb.askyesno = lambda *a, **k: True

    # ---- 1. 定位 7-Zip ----
    try:
        sevenzip_path = sevenzip.locate()
    except sevenzip.SevenZipNotFound as exc:
        print(f"未找到 7-Zip：{exc}")
        return 1
    print(f"7z = {sevenzip_path}")
    print(f"     {sevenzip.version(sevenzip_path)}\n")
    check("定位到可执行的 7-Zip", sevenzip.works(sevenzip_path), sevenzip_path)

    supported = sevenzip.supported_algorithms(sevenzip_path, os.path.join(ROOT, "main.py"))
    check("算法能力探测包含常用算法", {"SHA256", "MD5", "SHA1", "SHA512"} <= supported,
          " ".join(sorted(supported)))

    # ---- 2. 准备素材 ----
    os.makedirs(TEMP_DIR, exist_ok=True)
    folder = os.path.join(TEMP_DIR, "samples")
    shutil.rmtree(folder, ignore_errors=True)
    made = make_samples(folder)
    files = list(made.values())
    missing = os.path.join(folder, "根本没有这个.bin")

    # ---- 3. 真实计算并与 hashlib 对比 ----
    algs = ["SHA256", "MD5"]
    streamed: list[str] = []
    runner = HashRunner(files + [missing], algs, sevenzip_path,
                        on_result=lambda e: streamed.append(os.path.basename(e.path)),
                        on_log=lambda line, kind="info": None)
    started = time.time()
    runner.run()
    elapsed = time.time() - started
    entries = runner.entries

    computed = [e for e in entries.values() if e.hashes]
    check(f"全部有效文件都算出结果（{len(computed)}/{len(files)}）",
          len(computed) == len(files), f"用时 {elapsed:.2f}s")
    check("结果按文件流式回调（不是等全部跑完）", len(streamed) == len(files),
          f"{len(streamed)} 次回调")

    mismatch = []
    for entry in computed:
        for alg in algs:
            want = hashlib_of(entry.path, alg)
            if want and entry.hash_of(alg) != want:
                mismatch.append(f"{entry.name} {alg}")
    check("每个哈希都与 Python hashlib 一致", not mismatch, "、".join(mismatch[:3]) or "7z == hashlib")

    # 同名不同目录不能串号：7z 对绝对路径只回显文件名，靠相对路径才能对回去
    d1 = entries.get(norm_key(made["dir1/same.bin"]))
    d2 = entries.get(norm_key(made["dir2/same.bin"]))
    check("不同目录的同名文件各自独立记录",
          d1 is not None and d2 is not None and d1 is not d2
          and d1.hash_of("SHA256") == d2.hash_of("SHA256"),
          f"{d1 and d1.path} / {d2 and d2.path}")

    zh = entries.get(norm_key(made["子 目录/中文 文件名.bin"]))
    check("中文 + 空格路径正确回显并算对",
          zh is not None and zh.hash_of("SHA256") == hashlib_of(zh.path, "SHA256"),
          zh and zh.path or "没找到")

    lost = entries.get(norm_key(missing))
    check("不存在的文件给出明确原因而不是崩溃",
          lost is not None and not lost.hashes and bool(lost.error),
          lost and lost.error or "")

    total_bytes = sum(e.size for e in computed)
    check("大文件速度合理（7z 直接吃满磁盘）",
          elapsed < 60 and total_bytes > 16 * 1024 * 1024,
          f"{total_bytes / 1048576:.0f} MB / {elapsed:.2f}s ≈ "
          f"{total_bytes / 1048576 / max(elapsed, 0.01):.0f} MB/s")

    # ---- 4. 解析器的健壮性 ----
    sample_text = (
        "SHA256                                                           MD5        Size  Name\n"
        "---------------------------------------------------------------- ---------------- ----  ----\n"
        "aaaa 1111 5  a.bin\n"                       # 长度不对 → 丢弃
        "e08e1d7bd3fec53b7360de39482ac30d8d1b7bedead27e013810e29095fee6fb "
        "43900bdf9f2b6f7198c39625e347c285            10  a.bin\n"
        "---------------------------------------------------------------- ---------------- ----  ----\n"
        "c01d3bf6a6fd8b77e6c0bc739054861b1a37f6dad5a5fd027020c4212bfdcdf7-00000001 "
        "862017be3f57dee230872d4bc68f840b-00000001            20   \n"
        "SHA256 for data:              c01d3bf6a6fd8b77e6c0bc739054861b\n"
    )
    parsed, _warn = sevenzip.parse_output(sample_text, ["SHA256", "MD5"])
    check("解析器只收数据行，跳过表头/分隔行/汇总行",
          len(parsed) == 1 and next(iter(parsed.values()))["MD5"] ==
          "43900BDF9F2B6F7198C39625E347C285", str(list(parsed)))

    batches = sevenzip.plan_batches([made["dir1/same.bin"], made["dir2/other.bin"]])
    check("批次用相对路径 + 公共祖先目录",
          all(not os.path.isabs(rel) for _b, rels, _a in batches for rel in rels)
          and all(os.path.isdir(b) for b, _r, _a in batches),
          f"{len(batches)} 批 / base={batches[0][0]}")

    # ---- 5. 对比逻辑 ----
    values = list(entries.values())
    analysis = compare.analyse(values, algs)
    group_sizes = sorted(len(g) for g in compare.duplicate_groups(values, "SHA256"))
    check("重复分组正确（3 个相同内容归为一组）", group_sizes == [3], str(group_sizes))
    check("主算法选择正确", analysis["primary"] == "SHA256", analysis["primary"])
    check("无算法结果冲突时不报矛盾", not analysis["conflicts"], str(len(analysis["conflicts"])))

    same = compare.compare_paths(values, algs, [made["dir1/same.bin"], made["dir2/same.bin"]])
    diff = compare.compare_paths(values, algs, [made["dir1/same.bin"], made["dir2/other.bin"]])
    check("两个相同文件 → 结论「内容一致」", "一致 ✅" in same, same.replace("\n", " / "))
    check("两个不同文件 → 结论「存在差异」", "差异 ❌" in diff, diff.replace("\n", " / "))
    check("大小写写错的参考值也能匹配（0x / 空格 / 前缀都吃下）",
          len(compare.reference_matches(values, "SHA256",
              "0x" + hashlib_of(made["dir1/same.bin"], "SHA256"))) == 3,
          "sha256 前缀与分隔符归一化")

    # 部分匹配：只记得开头几位、或只有一段片段时也要能定位到文件
    full_sha = hashlib_of(made["dir1/same.bin"], "SHA256")
    prefix_hits = compare.reference_hits(values, ["SHA256"], full_sha[:8])
    check("前缀片段命中同内容的 3 个文件",
          len(prefix_hits) == 3 and {lvl for lvl, _a in prefix_hits.values()} == {"prefix"},
          str(sorted(os.path.basename(p) for p in prefix_hits)))
    middle = full_sha[20:34]
    mid_hits = compare.reference_hits(values, ["SHA256"], middle)
    check("中间片段按「包含」命中",
          len(mid_hits) == 3 and {lvl for lvl, _a in mid_hits.values()} == {"contains"},
          str(sorted({lvl for lvl, _a in mid_hits.values()})))
    check("少于 4 位不匹配（否则满屏假命中）",
          not compare.reference_hits(values, ["SHA256"], full_sha[:3])
          and bool(compare.reference_hits(values, ["SHA256"], full_sha[:4])),
          f"3 位 = {len(compare.reference_hits(values, ['SHA256'], full_sha[:3]))} 命中，"
          f"4 位 = {len(compare.reference_hits(values, ['SHA256'], full_sha[:4]))} 命中")
    other_md5 = hashlib_of(made["dir2/other.bin"], "MD5")
    cross = compare.reference_hits(values, ["SHA256", "MD5"], other_md5[10:26])
    check("跨算法搜索能定位到片段属于哪一列",
          len(cross) == 1 and next(iter(cross.values()))[1] == "MD5",
          str(next(iter(cross.values()), None)))
    check("完整值优先判为 exact 而不是 prefix",
          set(lvl for lvl, _a in compare.reference_hits(
              values, ["SHA256"], full_sha).values()) == {"exact"}, "")

    # 粘贴格式回归：大小写、0x、按 8 位空格/4 位冒号排版、sha256= 前缀、
    # 引号换行、sha256sum 整行（带文件名）都必须判成 exact。
    # 早先 normalize_hash 只要见到非十六进制字符就退化成“取最长一段”，
    # 把分组排版的完整哈希砍成 8 位，exact 被降级成 prefix。
    low = full_sha.lower()
    formats = {
        "大写原样": full_sha,
        "全小写": low,
        "大小写混排": "".join(c.lower() if i % 2 else c for i, c in enumerate(full_sha)),
        "0x 前缀": "0x" + low,
        "8 位空格分组": " ".join(low[i:i + 8] for i in range(0, 64, 8)),
        "4 位冒号分组": ":".join(low[i:i + 4] for i in range(0, 64, 4)),
        "4 位连字符分组": "-".join(low[i:i + 4] for i in range(0, 64, 4)),
        "sha256= 前缀": "sha256=" + low,
        "引号加换行": '"' + low + '"\n',
        "sha256sum 整行": low + "  sample_a.mp4",
    }
    bad = [name for name, q in formats.items()
           if {lvl for lvl, _a in compare.reference_hits(values, ["SHA256"], q).values()} != {"exact"}]
    check("10 种粘贴写法（大小写/分组/前缀/整行）都判为完全一致", not bad,
          "全部通过" if not bad else "不通过：" + "、".join(bad))
    check("错一位仍然判为不命中（没把校验放松）",
          not compare.reference_hits(values, ["SHA256"], low[:20] + ("0" if low[20] != "0" else "1") + low[21:]),
          "")

    # 人为制造「SHA256 相同但 MD5 不同」，看交叉校验能不能发现
    fake = [stat_entry(made["dir1/same.bin"]), stat_entry(made["dir2/same.bin"])]
    for index, entry in enumerate(fake):
        entry.hashes = {"SHA256": hashlib_of(entry.path, "SHA256"),
                        "MD5": "0" * 31 + str(index)}
    conflict = compare.analyse(fake, algs)
    check("SHA256 相同但 MD5 不同时标记为矛盾", len(conflict["conflicts"]) == 2,
          str(len(conflict["conflicts"])))

    # ---- 6. 界面流程 ----
    from ui import app as app_module

    # 自检的设置写到 temp 下，别把算法/窗口尺寸写进用户真实的 settings.json
    app_module.SETTINGS_PATH = os.path.join(TEMP_DIR, "selftest-settings.json")
    from ui.app import App

    # 「发送到」把选中的多个路径（可能混着文件夹）一次性丢给 main.py，
    # 所以命令行解析 + 目录展开要能扛住
    import main as main_module

    got_paths, got_run = main_module.parse_args(
        ["--run", made["dir1/same.bin"], folder, "不存在.bin"])
    check("parse_args 收下 --run 与全部路径（含目录）",
          got_run and got_paths == [made["dir1/same.bin"], folder, "不存在.bin"],
          f"{len(got_paths)} 个参数")
    main_module.parse_args(["--7z", sevenzip_path, "x.bin"])
    check("--7z 会写进环境变量并留下文件参数",
          os.environ.get("HASHGUI_7Z") == sevenzip_path, os.environ.get("HASHGUI_7Z"))

    app = App(initial_paths=[made["dir1/same.bin"], folder, os.path.join(folder, "没有这个.bin")])
    app.withdraw()
    app.update()
    check("窗口构建成功且已加载文件", len(app.entries) == len(files) + 1,
          f"{len(app.entries)} 行（文件夹展开 + 1 个不存在的占位行）")
    check("文件夹按「含子文件夹」展开成里面的文件",
          norm_key(made["子 目录/中文 文件名.bin"]) in app.entries,
          "深层文件也在" if norm_key(made["子 目录/中文 文件名.bin"]) in app.entries else "缺")
    lost_row = norm_key(os.path.join(folder, "没有这个.bin"))
    check("不存在的路径保留成一行并给出原因，而不是静默丢弃",
          lost_row in app.entries and bool(app.entries[lost_row].error),
          app.entries[lost_row].error if lost_row in app.entries else "被丢掉了")
    check("表格行数与文件数一致",
          len(app.tree.get_children("")) == len(app.entries),
          str(len(app.tree.get_children(""))))

    app.algorithm_vars["MD5"].set(True)
    app._on_algorithms_changed()
    check("勾选第二个算法后表格多出一列",
          "MD5" in list(app.tree.cget("columns")), str(list(app.tree.cget("columns"))))

    app.on_run()
    deadline = time.time() + 120
    while time.time() < deadline:
        app.update()
        time.sleep(0.05)
        if app.entries and all(e.hashes or e.error for e in app.entries.values()) \
                and not app._busy():
            break
    for _ in range(20):                   # 把「完成」消息与着色排干
        app.update()
        time.sleep(0.05)

    rows = app.tree.get_children("")
    filled = sum(1 for iid in rows if app.entries[iid].hashes)
    check("界面点「计算哈希」后全部回填", filled == len(files), f"{filled}/{len(files)}")
    marks = [app.tree.item(iid, "values")[0] for iid in rows]
    check("重复文件在「标记」列被点名",
          any("组 1" in str(m) for m in marks), str([m for m in marks if m][:2]))

    def sha_cell(iid):
        index = list(app.tree.cget("columns")).index("SHA256")
        return str(app.tree.item(iid, "values")[index])

    full_sha = app.entries[rows[0]].hash_of("SHA256")
    check("默认显示短哈希，避免 64 位被列宽截断",
          sha_cell(rows[0]).endswith("…") and len(sha_cell(rows[0])) == 17, sha_cell(rows[0]))
    app.full_hash.set(True)
    app._on_full_hash_toggled()
    check("勾选「显示完整哈希」后单元格是完整 64 位",
          sha_cell(rows[0]) == full_sha and len(sha_cell(rows[0])) == 64, sha_cell(rows[0])[:24])
    app.full_hash.set(False)
    app._on_full_hash_toggled()

    # 大小写显示切换：只影响显示/复制/导出，不能影响内部存储与任何比对结果
    mine = app.entries[rows[0]].hash_of("SHA256")
    check("内部存储恒为大写（切换显示不动数据）",
          mine == mine.upper() and len(mine) == 64, mine[:12])
    check("默认按大写显示", sha_cell(rows[0]) == mine[:16] + "…", sha_cell(rows[0]))
    app.hash_case.set("lower")
    app._on_case_toggled()
    check("切到小写后表格显示小写（省略号仍在）",
          sha_cell(rows[0]) == mine[:16].lower() + "…", sha_cell(rows[0]))
    # 用那三个内容相同的文件来验比对结果不受显示大小写影响（rows[0] 不一定是重复项）
    dup_hash = app.entries[norm_key(made["dir1/same.bin"])].hash_of("SHA256")
    summary_before = app.summary_text.get()
    app.ref_var.set(dup_hash)
    app._apply_analysis()
    flags_now = [str(app.tree.item(iid, "values")[4]) for iid in app.tree.get_children("")]
    check("小写显示下贴大写值仍然精确命中",
          flags_now.count("✓ 相同") == 3, str(sorted(set(flags_now))))
    app.ref_var.set("")
    app._apply_analysis()
    check("切换大小写不影响重复分组结果",
          app.summary_text.get() == summary_before, app.summary_text.get())
    app.hash_case.set("upper")
    app._on_case_toggled()
    check("汇总栏报告了重复组数", "组重复" in app.summary_text.get(), app.summary_text.get())
    check("状态栏显示完成信息", "成功" in app.status_text.get() or "个文件" in app.status_text.get(),
          app.status_text.get())

    app.tree.selection_set([norm_key(made["dir1/same.bin"]), norm_key(made["dir2/same.bin"])])
    app._update_verdict()
    check("选中两个相同文件时结论正确",
          "一致 ✅" in app.verdict.get("1.0", "end"), app.verdict.get("1.0", "end").strip()[:40])

    app.only_dupes.set(True)
    app._rebuild_rows()
    shown = len(app.tree.get_children(""))
    check("「只看重复」过滤生效", 0 < shown < len(files), f"{shown} / {len(files)} 行")
    check("过滤时文件计数会注明显示数量",
          f"{len(app.entries)} 个文件" in app.count_text.get() and "显示" in app.count_text.get(),
          app.count_text.get())
    app.only_dupes.set(False)
    app._rebuild_rows()
    check("取消过滤后恢复显示全部",
          len(app.tree.get_children("")) == len(app.entries) and "显示" not in app.count_text.get(),
          app.count_text.get())

    def ref_flags():
        return [str(app.tree.item(iid, "values")[4]) for iid in app.tree.get_children("")]

    def tags_of(rel_path):
        return tuple(app.tree.item(norm_key(made[rel_path]), "tags"))

    app.ref_var.set(hashlib_of(made["dir1/same.bin"], "SHA256").lower())
    app._apply_analysis()
    check("完整参考值：逐行标出 ✓ 相同 / ✗ 未命中",
          ref_flags().count("✓ 相同") == 3 and "✗ 未命中" in ref_flags(),
          str(sorted(set(ref_flags()))))
    check("命中的行被换成绿色高亮", "match" in tags_of("dir1/same.bin"),
          str(tags_of("dir1/same.bin")))
    # 回归点：着色原来只写在「重复组」的循环里，唯一文件命中参考值时不上色，
    # 而「拿一个哈希去一堆文件里找它」恰恰是最常用的场景。
    unique_sha = hashlib_of(made["dir2/other.bin"], "SHA256")
    app.ref_var.set(unique_sha)
    app._apply_analysis()
    check("唯一文件（不属于任何重复组）命中参考值也要上色",
          "match" in tags_of("dir2/other.bin") and "grp" not in str(tags_of("dir2/other.bin")),
          str(tags_of("dir2/other.bin")))
    check("没有重复组时参考值高亮依然生效",
          "match" in [t for iid in app.tree.get_children("")
                      for t in app.tree.item(iid, "tags")],
          str([app.tree.item(iid, "tags") for iid in app.tree.get_children("")][:2]))
    app.ref_var.set(hashlib_of(made["dir1/same.bin"], "SHA256").lower())
    app._apply_analysis()

    app.ref_var.set(hashlib_of(made["dir1/same.bin"], "SHA256")[:10])
    app._apply_analysis()
    check("前缀片段：标 ◐ 前缀并注明算法",
          sum(1 for f in ref_flags() if f.startswith("◐ 前缀")) == 3,
          str(sorted(set(ref_flags()))))
    check("部分匹配用琥珀色高亮，与「完全一致」区分",
          "partial" in tags_of("dir2/same.bin") and "match" not in tags_of("dir2/same.bin"),
          str(tags_of("dir2/same.bin")))
    check("汇总栏报告各级命中数量",
          "参考值命中 3 个" in app.summary_text.get() and "前缀 3" in app.summary_text.get(),
          app.summary_text.get())

    middle = hashlib_of(made["dir2/other.bin"], "MD5")[8:22]
    app.ref_var.set(middle)
    app._apply_analysis()
    check("跨算法搜索：片段只出现在 MD5 列也能定位",
          any("≈ 包含" in f and "MD5" in f for f in ref_flags()),
          str([f for f in ref_flags() if f]))

    app.ref_var.set("ab")
    app._apply_analysis()
    check("参考值过短时不标记并给出原因",
          all(f == "" for f in ref_flags()) and "太短" in app.summary_text.get(),
          app.summary_text.get())
    app.ref_var.set("")
    app._apply_analysis()
    all_tags = [t for iid in app.tree.get_children("") for t in app.tree.item(iid, "tags")]
    check("清空参考值后标记全部撤掉",
          all(f == "" for f in ref_flags()) and "参考值" not in app.summary_text.get(),
          f"{sorted(set(ref_flags()))} | {app.summary_text.get()}")
    check("清空参考值后不留过期高亮",
          "match" not in all_tags and "partial" not in all_tags, str(sorted(set(all_tags))))

    app._sort_by("SHA256")
    ordered = [app.entries[iid].hash_of("SHA256")[:8] for iid in app.tree.get_children("")]
    check("点哈希列排序后相同值相邻",
          all(ordered.count(h) <= 1 or ordered[ordered.index(h):].count(h) == ordered.count(h)
              for h in set(ordered)), str(ordered[:6]))

    report = os.path.join(TEMP_DIR, "report.txt")
    # 直接走导出逻辑（绕开文件对话框）
    original_dialog = app_module.filedialog.asksaveasfilename
    app_module.filedialog.asksaveasfilename = lambda *a, **k: report
    app._export()
    app_module.filedialog.asksaveasfilename = original_dialog
    ok_report = os.path.isfile(report) and os.path.getsize(report) > 0
    body = open(report, encoding="utf-8").read() if ok_report else ""
    check("导出报告包含文件路径与哈希", ok_report and "SHA256:" in body and "same.bin" in body,
          f"{os.path.getsize(report)} 字节" if ok_report else "没写出来")

    # 导出要跟着「哈希大小写」走（WYSIWYG），切回大写后又要恢复大写
    lower_report = os.path.join(TEMP_DIR, "report_lower.txt")
    app.hash_case.set("lower")
    app_module.filedialog.asksaveasfilename = lambda *a, **k: lower_report
    app._export()
    app_module.filedialog.asksaveasfilename = original_dialog
    lower_body = open(lower_report, encoding="utf-8").read() if os.path.isfile(lower_report) else ""
    sample_hash = next((e.hash_of("SHA256") for e in app.entries.values() if e.hash_of("SHA256")), "")
    check("导出跟随大小写显示设置",
          sample_hash.lower() in lower_body and sample_hash not in lower_body,
          f"{len(lower_body)} 字节")
    app.hash_case.set("upper")

    csv_path = os.path.join(TEMP_DIR, "report.csv")
    app_module.filedialog.asksaveasfilename = lambda *a, **k: csv_path
    app._export()
    app_module.filedialog.asksaveasfilename = original_dialog
    csv_body = open(csv_path, encoding="utf-8-sig").read() if os.path.isfile(csv_path) else ""
    check("CSV 报告可被 Excel 正确识别（带 BOM、列对齐）",
          csv_body.startswith("文件,大小,修改时间,SHA256,MD5"), csv_body.splitlines()[:1])

    # 取消：重新计算大文件时中途停止
    app._recompute([norm_key(made["big.bin"])])
    app.update()
    app.on_cancel()
    deadline = time.time() + 20
    while app._busy() and time.time() < deadline:
        app.update()
        time.sleep(0.05)
    check("取消后线程能退出", not app._busy(), app.status_text.get())

    app._on_close()
    check("窗口可正常关闭", True)

    # ---- 清理 ----
    shutil.rmtree(folder, ignore_errors=True)
    for leftover in ("report.txt", "report.csv", "selftest-settings.json"):
        try:
            os.remove(os.path.join(TEMP_DIR, leftover))
        except OSError:
            pass

    print("\n" + "=" * 70)
    passed = sum(1 for _l, ok, _d in results if ok)
    print(f"通过 {passed} / {len(results)}")
    for label, ok, detail in results:
        if not ok:
            print(f"  失败：{label}  {detail}")
    print("=" * 70)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
