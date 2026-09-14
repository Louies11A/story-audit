# -*- coding: utf-8 -*-
"""F08：问题处置记录、规则元数据与正文版本复核。"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from scripts import story_audit
from scripts.audit_state import AuditState, get_audit_state_path, save_audit_state


@pytest.fixture
def project():
    with TemporaryDirectory(prefix="story_audit_f08_") as directory:
        root = Path(directory)
        (root / "正文").mkdir()
        (root / "正文" / "第001章.txt").write_text(
            '第一章\n陆离握紧了钥匙。\n<!-- audit:p1 message="钥匙来源未交代" -->\n',
            encoding="utf-8",
        )
        yield root


def _state(project: Path) -> dict:
    return json.loads(get_audit_state_path(project / "reports").read_text(encoding="utf-8"))


def _snapshot(project: Path):
    return {
        path.relative_to(project).as_posix(): path.read_bytes() if path.is_file() else None
        for path in project.rglob("*")
    }


def _seed(project: Path, defects):
    save_audit_state(
        AuditState(
            chapter_versions={"1": "v1"},
            completed_chapters=[1.0],
            open_defects=list(defects),
        ),
        project / "reports",
    )


def _expert_defect(finding_id: str = "defect-expert-1", category: str = "prose"):
    return {
        "id": finding_id,
        "chapter": 1.0,
        "severity": "P2",
        "category": category,
        "issue": "文风偏翻译腔，段落节奏拖沓",
        "fix": "拆分长句，压缩说明性段落。",
        "source": "expert",
        "checker": "expert:排版质检",
        "status": "open",
    }


def _platform_defect(finding_id: str = "defect-platform-1"):
    return {
        "id": finding_id,
        "chapter": 1.0,
        "severity": "P1",
        "category": "platform",
        "issue": "知乎盐言第一人称穿帮",
        "fix": "统一为第一人称叙述。",
        "source": "deterministic",
        "checker": "platform_rubric",
        "platform": "zhihu",
        "status": "open",
        "rule": {
            "rule_id": "platform_rubric:zhihu",
            "rule_version": story_audit.RULE_METADATA_VERSION,
            "threshold": "见 references/rubrics/zhihu.md 对应门禁阈值",
            "condition": "zhihu 平台卡尺规则命中（第 001 章 全篇）",
            "context": "他走进庭院。",
        },
    }


def test_f08_deterministic_findings_carry_ids_and_rule_metadata(project):
    """确定性发现带稳定问题编号与规则 id/版本/阈值/命中条件/上下文。"""
    assert story_audit.audit_chapter(project, chapter_index=1, mode="solo", silent=True)[0] == 0
    state = _state(project)
    defect = next(item for item in state["open_defects"] if item.get("issue") == "钥匙来源未交代")
    assert defect["id"].startswith("defect-")
    rule = defect["rule"]
    assert rule["rule_id"] == "explicit_violation"
    assert rule["rule_version"] == story_audit.RULE_METADATA_VERSION
    assert rule["threshold"]
    assert rule["condition"]
    assert "钥匙来源未交代" in rule["context"]

    report = (project / "reports" / "单章审查" / "001-100章" / "第001章_审查报告.md").read_text(
        encoding="utf-8"
    )
    assert "规则 explicit_violation@" in report
    assert "命中条件：" in report and "阈值：" in report and "上下文：" in report


def test_f08_false_positive_dismissal_records_history(project):
    """非事实类误报处置：缺陷转入历史并保留原始发现与严重度，重复处置幂等。"""
    _seed(project, [_expert_defect()])
    assert story_audit.record_finding_disposition(
        project,
        finding_id="defect-expert-1",
        decision="false_positive",
        reason="作者判断为个人风格偏好",
        source="author",
        silent=True,
    )[0] == 0
    state = _state(project)
    assert state["open_defects"] == []
    resolved = [item for item in state["resolved_items"] if item.get("id") == "defect-expert-1"]
    assert len(resolved) == 1
    assert resolved[0]["severity"] == "P2" and resolved[0]["category"] == "prose"
    assert resolved[0]["issue"] == "文风偏翻译腔，段落节奏拖沓"
    assert resolved[0]["resolution"] == "false_positive_dismissed"
    assert resolved[0]["resolution_reason"] == "作者判断为个人风格偏好"
    assert resolved[0]["resolved_at"]

    dispositions = state["finding_dispositions"]
    assert len(dispositions) == 1
    assert dispositions[0]["decision"] == "false_positive"
    assert dispositions[0]["exempt"] is True
    assert dispositions[0]["text_version"]

    before = _snapshot(project)
    assert story_audit.record_finding_disposition(
        project,
        finding_id="defect-expert-1",
        decision="false_positive",
        reason="作者判断为个人风格偏好",
        source="author",
        silent=True,
    )[0] == 0
    assert _snapshot(project) == before

    code, payload = story_audit.get_finding_dispositions(project, silent=True)
    assert code == 0
    assert payload["counts"]["total"] == 1
    assert payload["dispositions"][0]["finding_status"] == "resolved_items"
    assert payload["dispositions"][0]["needs_reverification"] is False


def test_f08_factual_and_platform_findings_are_not_exempted(project):
    """事实冲突与平台门禁发现不得因作者偏好被免除。"""
    factual = _expert_defect("defect-factual-1", category="factual")
    _seed(project, [factual, _platform_defect()])
    for finding_id in ("defect-factual-1", "defect-platform-1"):
        assert story_audit.record_finding_disposition(
            project,
            finding_id=finding_id,
            decision="false_positive",
            reason="作者认为不影响阅读",
            source="author",
            silent=True,
        )[0] == 0

    state = _state(project)
    open_ids = {item.get("id") for item in state["open_defects"]}
    assert open_ids == {"defect-factual-1", "defect-platform-1"}
    for record in state["finding_dispositions"]:
        assert record["exempt"] is False
        assert record["retained_in_open_defects"] is True

    code, payload = story_audit.get_finding_dispositions(project, silent=True)
    assert code == 0
    assert payload["counts"]["exempt"] == 0
    assert all(item["finding_status"] == "open_defects" for item in payload["dispositions"])

    # 报告继承栏目明确保留在开放缺陷并展示规则元数据
    assert story_audit.audit_chapter(project, chapter_index=1, mode="solo", silent=True)[0] in (0, 1, 2)
    report = (project / "reports" / "LATEST_REPORT.md").read_text(encoding="utf-8")
    assert "仍保留在开放缺陷" in report
    assert "规则 platform_rubric:zhihu@" in report


def test_f08_needs_reverification_after_text_change(project):
    """正文变化后处置必须标记为需重新核验，且读取路径不改写状态。"""
    _seed(project, [_expert_defect()])
    assert story_audit.record_finding_disposition(
        project,
        finding_id="defect-expert-1",
        decision="deferred",
        reason="先记录，等修改后再核验",
        source="author",
        silent=True,
    )[0] == 0
    recorded_version = _state(project)["finding_dispositions"][0]["text_version"]
    assert recorded_version

    code, payload = story_audit.get_finding_dispositions(project, silent=True)
    assert code == 0
    assert payload["dispositions"][0]["needs_reverification"] is False

    state_path = get_audit_state_path(project / "reports")
    before = state_path.read_bytes()
    (project / "正文" / "第001章.txt").write_text("第一章\n他合上箱盖。\n", encoding="utf-8")
    code, payload = story_audit.get_finding_dispositions(project, silent=True)
    assert code == 0
    assert payload["dispositions"][0]["needs_reverification"] is True
    assert payload["counts"]["needs_reverification"] == 1
    assert state_path.read_bytes() == before
    assert payload["dispositions"][0]["severity"] == "P2"
    assert payload["dispositions"][0]["issue"] == "文风偏翻译腔，段落节奏拖沓"


def test_f08_same_evidence_relinks_existing_disposition(project):
    """相同证据（相同问题编号）能关联既有处置。"""
    assert story_audit.audit_chapter(project, chapter_index=1, mode="solo", silent=True)[0] == 0
    defect_id = next(
        item["id"] for item in _state(project)["open_defects"] if item.get("issue") == "钥匙来源未交代"
    )
    assert story_audit.record_finding_disposition(
        project,
        finding_id=defect_id,
        decision="deferred",
        reason="等待作者补齐来源",
        source="author",
        silent=True,
    )[0] == 0

    assert story_audit.audit_chapter(project, chapter_index=1, mode="solo", silent=True)[0] == 0
    again = next(
        item["id"] for item in _state(project)["open_defects"] if item.get("issue") == "钥匙来源未交代"
    )
    assert again == defect_id
    code, payload = story_audit.get_finding_dispositions(project, silent=True)
    assert code == 0
    assert payload["dispositions"][0]["finding_id"] == defect_id
    assert payload["dispositions"][0]["finding_status"] == "open_defects"
    assert payload["dispositions"][0]["needs_reverification"] is False


@pytest.mark.parametrize(
    "options",
    [
        {"finding_id": ""},
        {"finding_id": "defect-missing"},
        {"decision": "maybe"},
        {"reason": "   "},
        {"source": "model"},
    ],
)
def test_f08_invalid_dispositions_have_no_side_effects(project, options):
    _seed(project, [_expert_defect()])
    before = _snapshot(project)
    payload = {"finding_id": "defect-expert-1", "decision": "accepted", "reason": "确认问题"}
    payload.update(options)
    assert story_audit.record_finding_disposition(project, silent=True, **payload) == (3, Path(""))
    assert _snapshot(project) == before
