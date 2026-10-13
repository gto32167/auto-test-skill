#!/usr/bin/env python3
"""Read-only PRD source-text scanner assisting the original-text coverage review.

The case-design ledger (``normative_statements`` + ``atomic_rules``) is
reconciled against itself by the design gate, so a normative sentence that the
author never extracted from the PRD cannot be detected by that reconciliation.
This tool closes part of that blind spot heuristically: it splits the
user-provided PRD source into sentences, keeps those that look normative
(modal markers or quantity constraints), and matches them against the ledger
excerpts. It also flags atomic rules whose excerpt contains numeric
constraints while ``coverage_characteristics.boundaries`` stays undeclared.

It is a review aid, not a hard gate: every uncovered candidate requires a
disposition in the review document, but the scanner may both false-positive
(slogans, background sentences) and false-negative (heavily paraphrased
clauses), so it never replaces the manual chapter-by-chapter comparison.

Exit codes: 0 = report produced; 1 = invalid input (unreadable PRD files or
requirements file). Findings never change the exit code.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import re
import sys
import unicodedata
from pathlib import Path

import yaml

NORMATIVE_MARKERS = (
    "必须", "应当", "不得", "禁止", "不允许", "不能", "不可",
    "仅", "只能", "只允许", "只支持", "至少", "最多", "不超过", "不少于",
    "不低于", "不高于", "最长", "最短", "最高", "最低", "上限", "下限",
    "默认", "自动", "需要", "支持", "超过", "低于",
)

QUANTITY_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*(?:个|条|名|笔|次|层|级|人|天|日|周|个月|月|年|小时|分钟|秒"
    r"|元|万元|字|字符|位|张|件|页|行|列|GB|MB|KB|TB|%|％)"
)

SENTENCE_SPLIT = re.compile(r"[。！？!?；;\n\r]+")
CLAUSE_SPLIT = re.compile(r"[，,、；;：:]+")
PUNCT_STRIP = re.compile(
    r"[\s，,。.、;；:：!！?？\"'“”‘’（()）\[\]【】<>《》\-—_·…\n\r]+"
)

MIN_SENTENCE_LENGTH = 6
CHUNK_COVER_RATIO = 0.6

DISPOSITION_GUIDE = (
    "- mapped：确属规范性陈述但账本未收录 → 先补 normative_statements / atomic_rules，"
    "补齐后重新运行用例设计门禁\n"
    "- not_normative：非规范性陈述（背景、目标、口号等）→ 评审记录判断理由\n"
    "- duplicate：已由账本其他条款覆盖但相似度未达阈值 → 记录对应 statement_id / rule_id\n"
    "- pending：原文歧义无法确定 → 进待确认清单，不得静默改为 covered"
)


def text(value: object) -> str:
    return value if isinstance(value, str) else ("" if value is None else str(value))


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).lower()
    return PUNCT_STRIP.sub("", normalized)


def is_normative_candidate(sentence: str) -> bool:
    if any(marker in sentence for marker in NORMATIVE_MARKERS):
        return True
    if QUANTITY_PATTERN.search(sentence):
        return True
    return False


def excerpt_chunks(raw_excerpt: str) -> list[str]:
    chunks = []
    for chunk in CLAUSE_SPLIT.split(raw_excerpt):
        normalized = normalize_text(chunk)
        if len(normalized) >= MIN_SENTENCE_LENGTH:
            chunks.append(normalized)
    return chunks


def match_excerpts(
    sentence: str,
    excerpts: list[tuple[str, str]],
    min_similarity: float,
) -> tuple[bool, str, float]:
    """Match one sentence against the ledger.

    Returns ``(covered, best_ref, best_score)``. A sentence is covered when
    either side contains the other verbatim, when at least
    ``CHUNK_COVER_RATIO`` of the excerpt's clause chunks appear verbatim in
    the sentence, or when the normalized similarity reaches
    ``min_similarity``.
    """
    n_sentence = normalize_text(sentence)
    best_ref, best_score = "", 0.0
    for ref, raw_excerpt in excerpts:
        n_excerpt = normalize_text(raw_excerpt)
        if not n_excerpt:
            continue
        score = 0.0
        if len(n_excerpt) >= MIN_SENTENCE_LENGTH and (
            n_excerpt in n_sentence or n_sentence in n_excerpt
        ):
            score = 1.0
        else:
            chunks = excerpt_chunks(raw_excerpt)
            if chunks:
                hits = sum(1 for chunk in chunks if chunk in n_sentence)
                score = max(score, hits / len(chunks))
            score = max(
                score, difflib.SequenceMatcher(None, n_sentence, n_excerpt).ratio()
            )
        if score > best_score:
            best_ref, best_score = ref, score
    return best_score >= min_similarity, best_ref, best_score


def boundary_warnings(rules: list[dict]) -> list[dict]:
    findings = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        characteristics = rule.get("coverage_characteristics")
        if not isinstance(characteristics, dict):
            continue
        applicability = characteristics.get("applicability")
        if not isinstance(applicability, dict):
            continue
        boundary_relevant = (
            applicability.get("input") is True or applicability.get("comparison") is True
        )
        excerpt = text(rule.get("source_excerpt"))
        boundaries = characteristics.get("boundaries")
        declared = isinstance(boundaries, dict) and any(
            value not in (None, "", [], {}) for value in boundaries.values()
        )
        if boundary_relevant and re.search(r"\d", excerpt) and not declared:
            findings.append(
                {
                    "rule_id": text(rule.get("rule_id")),
                    "profile": text(characteristics.get("profile")),
                    "excerpt": excerpt[:80],
                    "issue": "摘录含数值约束但 boundaries 未声明，请核对是否漏提取边界（min/max/±1）",
                }
            )
    return findings


def read_document(path: Path) -> tuple[list[str], str | None]:
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown", ".txt"}:
        return path.read_text(encoding="utf-8", errors="replace").splitlines(), None
    if suffix == ".docx":
        try:
            import docx  # python-docx
        except ImportError:
            return [], "python-docx 未安装，无法解析 docx，请转换为 md/txt 后重试"
        document = docx.Document(str(path))
        return [paragraph.text for paragraph in document.paragraphs], None
    return [], f"不支持的文件类型: {path.name}"


def expand_prd_paths(inputs: list[str]) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    warnings: list[str] = []
    suffixes = {".md", ".markdown", ".txt", ".docx"}
    for raw in inputs:
        path = Path(raw)
        if path.is_dir():
            candidates = sorted(
                child for child in path.iterdir() if child.suffix.lower() in suffixes
            )
            if not candidates:
                warnings.append(f"目录中没有可扫描的 md/txt/docx 文件: {path}")
            files.extend(candidates)
        elif path.is_file():
            files.append(path)
        else:
            warnings.append(f"路径不存在: {path}")
    return files, warnings


def scan(
    prd_files: list[Path],
    rules: list[dict],
    excerpts: list[tuple[str, str]],
    min_similarity: float,
) -> tuple[list[dict], list[str], dict]:
    candidates: list[dict] = []
    load_errors: list[str] = []
    total_sentences = 0
    for path in prd_files:
        lines, error = read_document(path)
        if error:
            load_errors.append(f"{path.name}: {error}")
            continue
        for line_no, line in enumerate(lines, 1):
            for piece in SENTENCE_SPLIT.split(line):
                sentence = piece.strip()
                if len(normalize_text(sentence)) < MIN_SENTENCE_LENGTH:
                    continue
                total_sentences += 1
                if not is_normative_candidate(sentence):
                    continue
                covered, best_ref, best_score = match_excerpts(
                    sentence, excerpts, min_similarity
                )
                if not covered:
                    candidates.append(
                        {
                            "source": path.name,
                            "line": line_no,
                            "sentence": sentence,
                            "best_match": best_ref,
                            "best_score": round(best_score, 3),
                        }
                    )
    summary = {
        "files": [path.name for path in prd_files],
        "total_sentences": total_sentences,
        "uncovered_candidates": len(candidates),
        "ledger_excerpts": len(excerpts),
    }
    return candidates, load_errors, summary


def build_markdown_report(
    candidates: list[dict],
    boundary_findings: list[dict],
    summary: dict,
    load_errors: list[str],
) -> str:
    lines = [
        "# PRD 原文规范句扫描报告",
        "",
        f"- 生成时间: {_dt.datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"- 扫描文件: {len(summary['files'])} 份（{'、'.join(summary['files']) or '无'}）",
        f"- 扫描句子总数: {summary['total_sentences']}",
        f"- 疑似规范性候选未命中账本: {summary['uncovered_candidates']}",
        f"- 账本摘录条数: {summary['ledger_excerpts']}",
        f"- 边界启发式警告: {len(boundary_findings)}",
    ]
    if load_errors:
        lines.append(f"- 读取失败: {'；'.join(load_errors)}")
    lines += [
        "",
        "> 本报告为只读辅助评审工具，不是硬门禁。未命中候选不等于漏拆：扫描器可能",
        "> 误报（口号、背景句）也可能漏报（改写幅度过大的条款），机器清单不能替代",
        "> 人工逐章回比。原文覆盖专项评审必须对下列每个候选逐条给出 disposition。",
        "",
        "## 一、未命中候选清单（评审必须逐条处置）",
        "",
    ]
    if candidates:
        lines += [
            "| # | 来源 | 行号 | 原文句子 | 最相近账本条款(得分) | 处置 disposition |",
            "|---|---|---|---|---|---|",
        ]
        for index, item in enumerate(candidates, 1):
            sentence = item["sentence"].replace("|", "\\|")
            lines.append(
                f"| {index} | {item['source']} | {item['line']} | {sentence} "
                f"| {item['best_match'] or '—'}({item['best_score']}) | |"
            )
    else:
        lines.append("无未命中候选。")
    lines += ["", "处置口径：", "", DISPOSITION_GUIDE, ""]
    lines += ["## 二、边界启发式警告", ""]
    if boundary_findings:
        lines += [
            "| rule_id | profile | 问题 | 摘录片段 |",
            "|---|---|---|---|",
        ]
        for item in boundary_findings:
            excerpt = item["excerpt"].replace("|", "\\|")
            lines.append(
                f"| {item['rule_id']} | {item['profile']} | {item['issue']} | {excerpt} |"
            )
        lines += [
            "",
            "该警告只提示“疑似漏提取”，数值也可能是金额、数量等无需边界的语义；"
            "确认无需边界时在评审中记录理由即可。",
        ]
    else:
        lines.append("无边界启发式警告。")
    lines += [
        "",
        "## 三、覆盖统计",
        "",
        f"- 疑似规范性候选中未命中账本 {summary['uncovered_candidates']} 条；"
        f"账本摘录共 {summary['ledger_excerpts']} 条。",
        "- 请将本报告归档到项目目录（如 `review/source_scan.md`），并在评审文档中",
        "  引用处置结果；存在未处置候选时，原文覆盖专项评审视为未完成。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="扫描 PRD 原文中的疑似规范性句子并与条款账本比对（只读辅助评审）。"
    )
    parser.add_argument(
        "--prd", nargs="+", required=True,
        help="PRD 原文文件或目录（.md/.markdown/.txt/.docx）",
    )
    parser.add_argument(
        "--requirements", required=True, help="项目 05_requirements.yaml 路径"
    )
    parser.add_argument("--output", required=True, help="Markdown 报告输出路径")
    parser.add_argument("--output-yaml", help="可选：机器可读 YAML 结果路径")
    parser.add_argument(
        "--min-similarity", type=float, default=0.55,
        help="句子与摘录的归一化相似度阈值，默认 0.55",
    )
    args = parser.parse_args(argv)

    prd_files, warnings = expand_prd_paths(args.prd)
    if not prd_files:
        print("ERROR: 没有可扫描的 PRD 文件", file=sys.stderr)
        for warning in warnings:
            print(f"WARN: {warning}", file=sys.stderr)
        return 1

    requirements_path = Path(args.requirements)
    if not requirements_path.is_file():
        print(f"ERROR: 找不到 {args.requirements}", file=sys.stderr)
        return 1
    document = yaml.safe_load(requirements_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        print("ERROR: requirements 文件不是 YAML mapping", file=sys.stderr)
        return 1

    rules = [rule for rule in document.get("atomic_rules") or [] if isinstance(rule, dict)]
    excerpts: list[tuple[str, str]] = []
    for rule in rules:
        excerpt = text(rule.get("source_excerpt"))
        if excerpt:
            excerpts.append((f"atomic_rule:{text(rule.get('rule_id'))}", excerpt))
    for statement in document.get("normative_statements") or []:
        if not isinstance(statement, dict):
            continue
        excerpt = text(statement.get("source_excerpt"))
        if excerpt:
            excerpts.append((f"statement:{text(statement.get('statement_id'))}", excerpt))

    candidates, load_errors, summary = scan(
        prd_files, rules, excerpts, args.min_similarity
    )
    boundary_findings = boundary_warnings(rules)
    summary["uncovered_candidates"] = len(candidates)
    load_errors.extend(warnings)

    report = build_markdown_report(candidates, boundary_findings, summary, load_errors)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")

    if args.output_yaml:
        payload = {
            "summary": summary,
            "uncovered_candidates": candidates,
            "boundary_warnings": boundary_findings,
            "load_errors": load_errors,
        }
        yaml_path = Path(args.output_yaml)
        yaml_path.parent.mkdir(parents=True, exist_ok=True)
        yaml_path.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    print(
        f"source-scan: {summary['total_sentences']} sentences scanned, "
        f"{len(candidates)} uncovered candidates, "
        f"{len(boundary_findings)} boundary warnings -> {output_path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
