#!/usr/bin/env python3
"""AEO Engine · skill 可用性检查与暂存。

存在的唯一理由：**不许用自写 prompt 冒充 skill**。

产线里每个要写英文文案的环节都声明它依赖哪个 skill（config/outreach.yaml 的
skills.registry）。本脚本按 search_paths 逐个解析 SKILL.md：
  * 解析到 → 把真 skill 目录暂存进 <repo>/.claude/skills/<name>/，供 claude -p 加载。
             每次运行前先清空重建，所以暂存副本不会漂移——source 改了下次就跟着改。
  * 解析不到 → 报 missing。调用方（draft_runner）据此让对应环节 refuse，
             在草稿里留 [SKILL MISSING: <name>] 占位，绝不用自写 prompt 顶替。

为什么要暂存而不是直接让 claude 去读原路径：Claude Code 只从
  <cwd>/.claude/skills/ 与 ~/.claude/skills/ 发现 skill。
原路径（SynologyDrive 下的 Agents 目录）不在这两处，不暂存就加载不到。
暂存进仓库内的 .claude/skills（已 gitignore）而不是 ~/.claude/skills，
理由是不动用户的全局配置——那会影响他所有别的 claude 会话。

用法：
    python3 scripts/skill_check.py             # 只报告，不暂存
    python3 scripts/skill_check.py --stage     # 报告并暂存
    python3 scripts/skill_check.py --clean     # 清空暂存目录
退出码：0 = 全部必需 skill 齐备；3 = 有必需 skill 缺失（不是 1，1 留给真正的执行失败）
"""

import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aeo_common as ac  # noqa: E402

SCRIPT = "skill_check"
EXIT_SKILL_MISSING = 3


# 待补标记。skill 正文里出现它，说明该 skill 有已知缺口、处于降级状态。
INCOMPLETE_MARKER = "【待真人补】"


def resolve_skill(name, entry):
    """按 search_paths 找第一个含 SKILL.md 的目录。返回 (path or None, tried)。"""
    tried = []
    for p in entry.get("search_paths") or []:
        expanded = os.path.expanduser(p)
        skill_md = os.path.join(expanded, "SKILL.md")
        exists = os.path.isfile(skill_md)
        tried.append({"path": expanded, "has_SKILL_md": exists})
        if exists:
            return expanded, tried
    return None, tried


def scan_gaps(path):
    """数 SKILL.md 里的【待真人补】并摘出所在小节标题。

    为什么需要：文件存在 ≠ 内容完整。vivu-linkedin-rewriter 重建版里
    banned phrases 与 hook 模板两项原词表已丢失、标了待补，
    但只看 available=true 会以为它完全可用，产线就会在不知情的情况下按降级跑。
    这与 ai-writing-guideline 读不到实时规则时只在输出第一行说一句是同一类隐患：
    降级本身不可怕，降级没被看见才可怕。
    """
    if not path:
        return []
    md = os.path.join(path, "SKILL.md")
    if not os.path.isfile(md):
        return []
    gaps, section = [], "(文件开头)"
    with open(md, encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped.startswith("#"):
                section = stripped.lstrip("#").strip()
            if INCOMPLETE_MARKER in stripped:
                gaps.append({"section": section,
                             "line": stripped[:120]})
    return gaps


def stage(repo, stage_dir, resolved):
    """清空并重建暂存目录。返回落地的 skill 名列表。

    先整个删再复制：留着上一轮的残留会让「skill 已被移除」这件事看不出来。
    """
    target = os.path.join(repo, stage_dir)
    if os.path.isdir(target):
        shutil.rmtree(target)
    os.makedirs(target, exist_ok=True)
    staged = []
    for name, src in resolved.items():
        if not src:
            continue
        shutil.copytree(src, os.path.join(target, name))
        staged.append(name)
    return staged, target


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage", action="store_true", help="解析后暂存进 .claude/skills/")
    parser.add_argument("--clean", action="store_true", help="只清空暂存目录后退出")
    args = parser.parse_args()

    cfg = ac.load_config("outreach.yaml")
    skills_cfg = cfg["skills"]
    stage_dir = skills_cfg["stage_dir"]

    if args.clean:
        target = os.path.join(ac.REPO, stage_dir)
        if os.path.isdir(target):
            shutil.rmtree(target)
        print(json.dumps({"script": SCRIPT, "cleaned": target}, ensure_ascii=False, indent=2))
        return 0

    registry = skills_cfg["registry"]
    report, resolved, missing_required = {}, {}, []

    degraded = []
    for name, entry in registry.items():
        path, tried = resolve_skill(name, entry)
        resolved[name] = path
        gaps = scan_gaps(path)
        report[name] = {
            "available": bool(path),
            # available 说的是「文件在」，complete 说的是「内容没缺口」。
            # 两个都要看——只看前者会把降级状态读成完全可用。
            "complete": bool(path) and not gaps,
            "known_gaps": gaps,
            "resolved_path": path,
            "required": bool(entry.get("required")),
            "used_by": entry.get("used_by") or [],
            "searched": tried,
        }
        if not path and entry.get("required"):
            missing_required.append(name)
        elif gaps:
            degraded.append(name)

    result = {
        "script": SCRIPT,
        "stage_dir": stage_dir,
        "skills": report,
        "missing_required": missing_required,
        # 调用方按 used_by 反查：哪些环节因为缺 skill 必须 refuse
        "blocked_steps": sorted({
            step
            for name in missing_required
            for step in (registry[name].get("used_by") or [])
        }),
        # 降级不等于阻断：skill 在，但有已知缺口，产线可以跑但必须在产出里明说降了什么。
        "degraded_skills": degraded,
        "degraded_steps": sorted({
            step
            for name in degraded
            for step in (registry[name].get("used_by") or [])
        }),
    }

    if args.stage:
        staged, target = stage(ac.REPO, stage_dir, resolved)
        result["staged"] = staged
        result["staged_into"] = target

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return EXIT_SKILL_MISSING if missing_required else 0


if __name__ == "__main__":
    sys.exit(main())
