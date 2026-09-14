# -*- coding: utf-8 -*-
"""F04：专家结果契约、执行状态汇总与幂等归档协议。"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from scripts import story_audit
from scripts.audit_state import get_audit_state_path
from scripts.expert_results import (
    EXPERT_ADVERSARIAL_CRITIC,
    EXPERT_ASSET_AUDITOR,
    EXPERT_CONTINUITY_GUARD,
    EXPERT_STATUS_COMPLETED,
    EXPERT_STATUS_FAILED,
    EXPERT_STATUS_NOT_EXECUTED,
    EXPERT_STYLE_RHYTHM,
    ExpertResult,
    compute_text_fingerprint,
    get_expert_result_store_path,
    get_expert_summary_path,
    load_expert_result_records,
)
from scripts.types import Finding


CHAPTER_ONE = "第一章\n他握紧了钥匙。\n"
CHAPTER_TWO = "第二章\n海面雾气未散。\n"
ALL_EXPERTS = (
    EXPERT_ASSET_AUDITOR,
    EXPERT_CONTINUITY_GUARD,
    EXPERT_STYLE_RHYTHM,
    EXPERT_ADVERSARIAL_CRITIC,
)


@pytest.fixture
def project():
    with TemporaryDirectory(prefix="story_audit_f04_") as directory:
        root = Path(directory)
        (root / "正文").mkdir()
        (root / "正文" / "第001章.txt").write_text(CHAPTER_ONE, encoding="utf-8")
        (root / "正文" / "第002章.txt").write_text(CHAPTER_TWO, encoding="utf-8")
        yield root


def _state(project: Path) -> dict:
    return json.loads(get_audit_state_path(project / "reports").read_text(encoding="utf-8"))


def _issues(project: Path):
    return [item.get("issue") for item in _state(project)["open_defects"]]


def _snapshot(project: Path):
    return {
        path.relative_to(project).as_posix(): path.read_bytes() if path.is_file() else None
        for path in project.rglob("*")
    }


def _chapter_text(project: Path, chapter: int) -> str:
    return (project / "正文" / f"第{chapter:03d}章.txt").read_text(encoding="utf-8")


def _fingerprint(project: Path, *chapters: int) -> str:
    return compute_text_fingerprint(
        {float(chapter): _chapter_text(project, chapter) for chapter in chapters}
    )


def _finding(issue: str, severity: str = "P1", category: str = "factual") -> Finding:
    return Finding(
        severity=severity,
        category=category,
        location="第001章 行2",
        evidence="他握紧了钥匙。",
        issue=issue,
        fix="【事实对齐】补齐来源交代。",
    )


def _result(**overrides) -> ExpertResult:
    payload = {
        "expert": EXPERT_ASSET_AUDITOR,
        "status": EXPERT_STATUS_COMPLETED,
        "chapters": [1],
        "text_fingerprint": "",
    }
    payload.update(overrides)
    return ExpertResult(**payload)


def test_f04_contract_validates_status_fingerprint_and_findings(project):
    with pytest.raises(ValueError):
        _result(status="done", text_fingerprint="abc")
    with pytest.raises(ValueError):
        _result(text_fingerprint="")
    with pytest.raises(ValueError):
        _result(expert="", status=EXPERT_STATUS_FAILED)
    with pytest.raises(ValueError):
        _result(status=EXPERT_STATUS_NOT_EXECUTED, findings=[_finding("未执行不应有发现")])
    with pytest.raises(ValueError):
        _result(status=EXPERT_STATUS_FAILED, chapters=["1"])
    with pytest.raises(ValueError):
        _result(status=EXPERT_STATUS_FAILED, chapters=[])
    with pytest.raises(ValueError):
        _result(text_fingerprint="abc", findings=[{"severity": "P9", "category": "factual"}])

    result = _result(
        text_fingerprint=_fingerprint(project, 1),
        findings=[_finding("钥匙来源未交代")],
        evidence="第1章 行2",
    )
    restored = ExpertResult.from_dict(result.to_dict())
    assert restored.expert == result.expert
    assert restored.status == EXPERT_STATUS_COMPLETED
    assert restored.chapters == [1.0]
    assert restored.text_fingerprint == result.text_fingerprint
    assert [item.issue for item in restored.findings] == ["钥匙来源未交代"]
    assert restored.scope_label == "第1章"
    assert _result(chapters=[1, 2], text_fingerprint="c").scope_label == "第1-2章"
    # 契约与指纹工具通过 story_audit 公开入口导出。
    assert story_audit.ExpertResult is ExpertResult
    assert story_audit.compute_text_fingerprint is compute_text_fingerprint

def test_f04_summary_follows_report_after_text_reverts(project):
    """P3-2：正文回退到已审指纹后，报告与 EXPERT_SUMMARY.md 必须一致地回到未过期。"""
    version = _fingerprint(project, 1)
    result = _result(text_fingerprint=version, findings=[_finding("回退一致性结论")])
    assert story_audit.archive_expert_results(project, results=[result], silent=True)[0] == 0
    summary_path = get_expert_summary_path(project / "reports")

    (project / "正文" / "第001章.txt").write_text(CHAPTER_ONE + "他合上了箱盖。\n", encoding="utf-8")
    assert story_audit.audit_chapter(project, chapter_index=1, silent=True)[0] == 0
    assert "需重新执行专家审查" in summary_path.read_text(encoding="utf-8")

    (project / "正文" / "第001章.txt").write_text(CHAPTER_ONE, encoding="utf-8")
    code, report = story_audit.audit_chapter(project, chapter_index=1, silent=True)
    assert code == 0
    report_text = report.read_text(encoding="utf-8")
    summary_text = summary_path.read_text(encoding="utf-8")
    assert "需重新执行专家审查" not in report_text
    assert "需重新执行专家审查" not in summary_text
    assert "完成" in summary_text
    assert "已过期：0" in summary_text


def test_f04_archive_presents_all_execution_states_in_summary_and_reports(project, capsys):
    results = [
        _result(
            text_fingerprint=_fingerprint(project, 1),
            findings=[_finding("钥匙来源未交代")],
            evidence="第1章 行2",
        ),
        _result(
            expert=EXPERT_CONTINUITY_GUARD,
            chapters=[1, 2],
            text_fingerprint=_fingerprint(project, 1, 2),
        ),
        _result(expert=EXPERT_STYLE_RHYTHM, status=EXPERT_STATUS_FAILED, reason="宿主执行超时"),
        _result(
            expert=EXPERT_ADVERSARIAL_CRITIC,
            status=EXPERT_STATUS_NOT_EXECUTED,
            chapters=[2],
            reason="宿主未调度",
        ),
    ]
    code, summary_path = story_audit.archive_expert_results(project, results=results, silent=True)
    assert code == 0
    assert summary_path == get_expert_summary_path(project / "reports")
    assert summary_path.is_file()

    summary = summary_path.read_text(encoding="utf-8")
    for expert in ALL_EXPERTS:
        assert expert in summary
    assert "完成" in summary and "失败" in summary and "未执行" in summary
    assert "宿主执行超时" in summary
    assert "第1-2章" in summary

    records = load_expert_result_records(get_expert_result_store_path(project / "reports"))
    assert len(records) == 4
    by_expert = {record["expert"]: record for record in records}
    assert by_expert[EXPERT_ASSET_AUDITOR]["status"] == EXPERT_STATUS_COMPLETED
    assert by_expert[EXPERT_STYLE_RHYTHM]["status"] == EXPERT_STATUS_FAILED
    assert by_expert[EXPERT_ADVERSARIAL_CRITIC]["status"] == EXPERT_STATUS_NOT_EXECUTED
    assert by_expert[EXPERT_ASSET_AUDITOR]["stale"] is False
    assert by_expert[EXPERT_ADVERSARIAL_CRITIC]["stale"] is False

    # 已完成的专家 P0/P1 发现进入持久状态，来源标记为 expert。
    defects = [item for item in _state(project)["open_defects"] if item.get("issue") == "钥匙来源未交代"]
    assert len(defects) == 1
    assert defects[0]["source"] == "expert"
    assert defects[0]["checker"] == f"expert:{EXPERT_ASSET_AUDITOR}"

    # 后续报告与 inherited items 同时呈现执行状态与专家发现。
    code_audit, report = story_audit.audit_chapter(project, chapter_index=2, silent=True)
    assert code_audit == 0
    report_text = report.read_text(encoding="utf-8")
    assert "专家执行状态" in report_text
    for expert in ALL_EXPERTS:
        assert expert in report_text
    assert "失败" in report_text and "未执行" in report_text
    assert "钥匙来源未交代" in report_text
    bundle = json.loads(
        (project / "reports" / ".cache" / "pre_audit_bundle.json").read_text(encoding="utf-8")
    )
    assert "钥匙来源未交代" in json.dumps(bundle["inherited_items"], ensure_ascii=False)
    assert "失败" in json.dumps(bundle["inherited_items"], ensure_ascii=False)

    # F01 合并语义：确定性复审不得清除专家来源记录。
    assert story_audit.audit_chapter(project, chapter_index=1, silent=True)[0] == 0
    assert "钥匙来源未交代" in _issues(project)
    survived = next(
        item for item in _state(project)["open_defects"] if item.get("issue") == "钥匙来源未交代"
    )
    assert survived["source"] == "expert"

    # 批量大盘报告走同一继承栏目，同样呈现专家执行状态。
    code_scope, scope_report = story_audit.audit_scope(project, "2-2", silent=True)
    assert code_scope == 0
    scope_text = scope_report.read_text(encoding="utf-8")
    assert "专家执行状态" in scope_text
    assert EXPERT_STYLE_RHYTHM in scope_text
    assert "钥匙来源未交代" in scope_text
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_f04_execution_states_render_without_any_defect(project):
    """没有开放缺陷与伏笔时，专家执行状态仍必须出现在报告中。"""
    results = [
        _result(text_fingerprint=_fingerprint(project, 1)),
        _result(
            expert=EXPERT_ADVERSARIAL_CRITIC,
            status=EXPERT_STATUS_NOT_EXECUTED,
            chapters=[2],
            reason="宿主未调度",
        ),
    ]
    assert story_audit.archive_expert_results(project, results=results, silent=True)[0] == 0
    assert _state(project)["open_defects"] == []

    code, report = story_audit.audit_chapter(project, chapter_index=2, silent=True)
    assert code == 0
    report_text = report.read_text(encoding="utf-8")
    assert "专家执行状态" in report_text
    assert EXPERT_ASSET_AUDITOR in report_text
    assert "未执行" in report_text


def test_f04_duplicate_submission_is_idempotent(project):
    result = _result(text_fingerprint=_fingerprint(project, 1), findings=[_finding("钥匙来源未交代")])
    assert story_audit.archive_expert_results(project, results=[result], silent=True)[0] == 0
    store = get_expert_result_store_path(project / "reports")
    store_before = store.read_bytes()
    state_before = _state(project)

    assert story_audit.archive_expert_results(project, results=[result], silent=True)[0] == 0
    assert store.read_bytes() == store_before
    assert _state(project) == state_before
    assert len(load_expert_result_records(store)) == 1
    assert _issues(project).count("钥匙来源未交代") == 1
    summary = get_expert_summary_path(project / "reports").read_text(encoding="utf-8")
    assert "记录：1 条" in summary

    # 同一次调用内重复提交同一条结果同样只保留一份。
    assert story_audit.archive_expert_results(project, results=[result, result], silent=True)[0] == 0
    assert len(load_expert_result_records(store)) == 1
    assert _issues(project).count("钥匙来源未交代") == 1


def test_f04_stale_result_is_marked_and_cannot_override_current_verdict(project):
    old_version = _fingerprint(project, 1)
    assert story_audit.archive_expert_results(
        project,
        results=[_result(text_fingerprint=old_version, findings=[_finding("旧的资源缺口")])],
        silent=True,
    )[0] == 0
    assert "旧的资源缺口" in _issues(project)

    (project / "正文" / "第001章.txt").write_text(CHAPTER_ONE + "他合上了箱盖。\n", encoding="utf-8")
    stale_result = _result(
        text_fingerprint=old_version, findings=[_finding("过期结论不应覆盖当前裁决")]
    )
    assert story_audit.archive_expert_results(project, results=[stale_result], silent=True)[0] == 0

    records = load_expert_result_records(get_expert_result_store_path(project / "reports"))
    stale_records = [record for record in records if record["stale"]]
    assert len(stale_records) == 1
    assert stale_records[0]["stale_reason"]
    assert "过期结论不应覆盖当前裁决" not in _issues(project)
    summary = get_expert_summary_path(project / "reports").read_text(encoding="utf-8")
    assert "已过期" in summary

    fresh_result = _result(
        text_fingerprint=_fingerprint(project, 1), findings=[_finding("新版本发现的资源问题")]
    )
    assert story_audit.archive_expert_results(project, results=[fresh_result], silent=True)[0] == 0
    assert "新版本发现的资源问题" in _issues(project)


def test_f04_only_p0_p1_findings_enter_persistent_state(project):
    result = _result(
        text_fingerprint=_fingerprint(project, 1),
        findings=[
            _finding("专家 P0 结论", severity="P0", category="causal"),
            _finding("专家 P1 结论", severity="P1", category="consistency"),
            _finding("专家 P2 建议", severity="P2", category="prose"),
        ],
    )
    assert story_audit.archive_expert_results(project, results=[result], silent=True)[0] == 0
    issues = _issues(project)
    assert "专家 P0 结论" in issues
    assert "专家 P1 结论" in issues
    assert "专家 P2 建议" not in issues


@pytest.mark.parametrize(
    "results",
    [
        [],
        None,
        "bad",
        [{"expert": "", "status": "completed", "chapters": [1], "text_fingerprint": "abc"}],
        [{"expert": "账本专员", "status": "completed", "chapters": [1]}],
        [{"expert": "账本专员", "status": "not_executed", "chapters": [1], "findings": [{"severity": "P0"}]}],
    ],
)
def test_f04_invalid_submission_has_no_side_effects(project, results):
    before = _snapshot(project)
    assert story_audit.archive_expert_results(project, results=results, silent=True) == (3, Path(""))
    assert _snapshot(project) == before


def test_f04_corrupt_store_blocks_before_writes(project):
    store = get_expert_result_store_path(project / "reports")
    store.parent.mkdir(parents=True)
    store.write_text("{broken", encoding="utf-8")
    before = _snapshot(project)
    assert story_audit.archive_expert_results(
        project, results=[_result(text_fingerprint=_fingerprint(project, 1))], silent=True
    ) == (3, Path(""))
    assert _snapshot(project) == before


@pytest.mark.parametrize("bad_project", [None, 123, "", "missing"])
def test_f04_invalid_project_dir_is_controlled(project, bad_project):
    before = _snapshot(project)
    target = project / "missing" if bad_project == "missing" else bad_project
    assert story_audit.archive_expert_results(
        target, results=[_result(text_fingerprint="abc")], silent=True
    ) == (3, Path(""))
    assert _snapshot(project) == before


def test_f04_staleness_is_recomputed_in_reports_and_summary(project):
    """P2-2：正文变更后，读取/呈现路径按当前正文重新判定过期状态。"""
    old_version = _fingerprint(project, 1)
    result = _result(text_fingerprint=old_version, findings=[_finding("首次提交的专家结论")])
    assert story_audit.archive_expert_results(project, results=[result], silent=True)[0] == 0
    store = get_expert_result_store_path(project / "reports")
    store_before = store.read_bytes()
    summary_path = get_expert_summary_path(project / "reports")
    summary_before = summary_path.read_text(encoding="utf-8")
    assert "需重新执行专家审查" not in summary_before
    assert "已过期：0" in summary_before

    (project / "正文" / "第001章.txt").write_text(CHAPTER_ONE + "他合上了箱盖。\n", encoding="utf-8")
    code, report = story_audit.audit_chapter(project, chapter_index=1, silent=True)
    assert code == 0
    report_text = report.read_text(encoding="utf-8")
    assert "需重新执行专家审查" in report_text
    assert "已过期" in report_text

    # 批量大盘报告走同一读取/呈现路径，同样按当前正文重新判定。
    code_scope, scope_report = story_audit.audit_scope(project, "1-2", silent=True)
    assert code_scope == 0
    assert "需重新执行专家审查" in scope_report.read_text(encoding="utf-8")

    summary_after = summary_path.read_text(encoding="utf-8")
    assert "需重新执行专家审查" in summary_after
    assert "已过期：1" in summary_after
    # 读取/呈现路径不得改写归档 JSON。
    assert store.read_bytes() == store_before
    # 呈现层刷新不会二次注入缺陷：原记录只保留一份。
    assert _issues(project).count("首次提交的专家结论") == 1

    stale_submit = _result(
        text_fingerprint=old_version, findings=[_finding("过期结论不得进入持久状态")]
    )
    assert story_audit.archive_expert_results(project, results=[stale_submit], silent=True)[0] == 0
    assert "过期结论不得进入持久状态" not in _issues(project)


def test_f04_corrupt_store_blocks_audit_before_any_write(project):
    """P3-1：专家归档损坏时必须在账本等任何写入之前失败。"""
    (project / "正文" / "第001章.txt").write_text(
        CHAPTER_ONE
        + '<!-- audit:stash name="海门钥匙" origin="第1章" status="pending" -->\n',
        encoding="utf-8",
    )
    store = get_expert_result_store_path(project / "reports")
    store.parent.mkdir(parents=True)
    store.write_text("{broken", encoding="utf-8")
    before = _snapshot(project)

    code, report = story_audit.audit_chapter(project, chapter_index=1, silent=True)
    assert (code, report) == (3, Path(""))
    assert _snapshot(project) == before
    assert not (project / "设定" / "资源账本.json").exists()
    assert not (project / "资源账本.json").exists()
    assert not (project / "reports" / ".audit_state.json").exists()


def test_f04_expert_defect_identity_includes_platform(project):
    """P3-3：专家缺陷身份包含平台，跨平台记录可区分，同平台重复归档仍幂等。"""
    result = _result(
        text_fingerprint=_fingerprint(project, 1),
        findings=[_finding("平台相关专家结论", severity="P0", category="platform")],
    )
    assert story_audit.archive_expert_results(
        project, results=[result], platform="generic", silent=True
    )[0] == 0
    assert story_audit.archive_expert_results(
        project, results=[result], platform="zhihu", silent=True
    )[0] == 0

    def platform_defects():
        return [
            item
            for item in _state(project)["open_defects"]
            if item.get("issue") == "平台相关专家结论"
        ]

    defects = platform_defects()
    assert sorted(item["platform"] for item in defects) == ["generic", "zhihu"]
    assert len({item["id"] for item in defects}) == 2

    assert story_audit.archive_expert_results(
        project, results=[result], platform="zhihu", silent=True
    )[0] == 0
    assert len(platform_defects()) == 2

    code, report = story_audit.audit_chapter(project, chapter_index=2, silent=True)
    assert code == 0
    report_text = report.read_text(encoding="utf-8")
    assert "专家：generic" in report_text
    assert "专家：zhihu" in report_text
