"""
资源账本状态机、多主体所有权与防脏写引擎 (ledger_engine.py)

功能职责：
1. 统一七类资产数据模型与九种状态扩展状态机；
2. 支持多主体所有权与持有权分离（借出、归还、转让）及全流程变迁流水记账；
3. 伏笔缓冲池扫描器，正则提取正文注释中的伏笔标签；
4. 防脏写覆盖拦截器 (Dirty-Write Guard)，拦截外部未同步的 Markdown 篡改；
5. 冷热资产分层 Markdown 渲染，兼顾高频阅读与低频归档；
6. 分卷封账快照与期末结转；
7. 从 Markdown 反向增量同步更新 JSON 数据源。
"""

import hashlib
import json
import math
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple, Union

from scripts.safe_io import SafeIOWriteError, read_file_safe, write_file_safe

# 七类资产标准分类
ASSET_CATEGORIES: Set[str] = {
    "装备道具",
    "丹药耗材",
    "资金资产",
    "功法神通",
    "随行战力",
    "地契房产",
    "身份特权",
    "身份权限",  # 兼容旧分类
    "规则诡器",
    "全局状态",
}

# 扩展状态机状态集合
ASSET_STATUSES: Set[str] = {
    "UNACQUIRED",   # 未获取（线索/伏笔阶段）
    "ACQUIRED",     # 已获取（在背包/仓库中）
    "EQUIPPED",     # 已装备/生效中
    "CONSUMED",     # 已消耗完毕
    "DAMAGED",      # 已破损/受损失效
    "TRANSFERRED",  # 已永久转移所有权
    "LENT_OUT",     # 已借出（所有权未变，持有者变更）
    "RECLAIMED",    # 已收回（物归原主）
    "RESTORED",     # 已修复/复原
}



# 冷资产保护集合 (P2-04: Markdown 同步时防意外抹除)
COLD_ASSET_STATUSES: Set[str] = {
    "CONSUMED",
    "DAMAGED",
    "TRANSFERRED",
}

# 启发式抽取顶层预编译常量 (P2-02 优化)
HEURISTIC_UNITS_REGEX = (
    "门|座|台|艘|架|挺|只|箱|吨|斤|两|发|枚|颗|件|套|把|支|组|部|瓶|袋|罐|筒|具|"
    "块|根|张|联|卷|桶|批|口|尊|点|亩|顷|间|栋|处|份|笔|宗|所|文|贯|元|分|角|个|"
    "株|粒|股|成|页|册|柄|片|节|段|方|条|道|缕|丝|面|盏|贴|尺|寸|升|斗|石|匹|包|捆|扎|提|票|炉|鼎|副"
)

HEURISTIC_BRACKET_PATTERN = re.compile(
    r'【(?P<header>[^】]*?(?:收录|发现|获得|激活|建造|升级|开启|解锁|掉落|装备|制造|打捞|缴获|入库|分得|分家|签约|奖励|结算|收获|进账|清点|盘点|核算|收容|继承|采购|购置)[^】]*?)[：:\s]*(?P<content>[^】]+)】'
)

HEURISTIC_NUMS_REGEX = r"(?:\d+(?:\.\d+)?|[一二两三四五六七八九十百千万半]+|上百|上千|上万|百|千|万)"

HEURISTIC_KWS_REGEX = (
    # 1. 仙侠玄幻 / 修真
    r"(?:筑基丹|聚气丹|破境丹|培元丹|还魂丹|洗髓丹|补血丹|灵丹|丹药|灵石|下品灵石|中品灵石|上品灵石|极品灵石|"
    r"灵草|灵药|灵芝|人参|兽核|妖丹|灵晶|灵液|灵泉|灵髓|龙血|凤羽|玄铁|秘银|"
    r"飞剑|灵剑|灵器|法宝|灵宝|乾坤袋|储物袋|空间戒指|阵旗|阵盘|功法|秘籍|心法|剑诀|拳谱|残卷|身法|禁术|传承|"
    # 2. 科幻末世 / 军工装备
    r"速射炮|主炮|机炮|舰炮|近防炮|火炮|高射炮|迫击炮|加农炮|重炮|防空炮|火箭炮|"
    r"鱼雷|导弹|火箭弹|深弹|穿甲弹|高爆弹|曳光弹|燃烧弹|子弹|炮弹|手雷|地雷|水雷|"
    r"步枪|突击步枪|冲锋枪|狙击步枪|机枪|手枪|猎枪|霰弹枪|火箭筒|发射巢|发射管|发射器|"
    r"防盾|军火箱|弹药箱|弹药|军火|装甲|骨甲|防弹衣|防弹插板|战术背心|夜视仪|消音器|瞄准镜|刺刀|枪塔|"
    r"数控机床|五轴机床|五轴数控机床|机床|工业母机|发电机|柴油机|燃气轮机|发动机|电动机|增压机|汽油机|充电机|"
    r"水泵|抽水机|空压机|潜水器|水肺|呼吸器|浮力气囊|气囊|千斤顶|电动绞盘|绞盘|铣刀|合金铣刀|焊机|电焊机|车床|"
    r"蓄电池|储能电池|变压器|配电柜|轴系|舵机|螺旋桨|喷水推进器|推进器|相控阵声呐|声呐基阵|声呐|相控阵|水听器|"
    r"火控系统|火控雷达|火控计算机|通信基站|无线电台|对讲机|巡逻艇|双体炮艇|炮艇|快艇|双体船|冲锋舟|皮划艇|防弹艇|救生艇|"
    r"拖轮|驳船|货轮|护卫舰|驱逐舰|巡洋舰|战列舰|潜艇|潜航器|装甲车|重卡|"
    r"大米|白面|面粉|小麦|糙米|粗粮|肉罐头|水果罐头|蔬菜罐头|鱼罐头|罐头|压缩饼干|单兵口粮|军粮|口粮|"
    r"纯净水|矿泉水|纯水|抗生素|消炎药|止痛药|急救包|重油|柴油|汽油|航空煤油|机油|润滑油|防冻液|"
    r"防弹钢|特种防弹钢|特种钢|钛合金|铝合金|钨钢|无缝钢管|钢材|钢板|"
    r"重构点|进化核心|蓝图|改装蓝图|建造蓝图|设计图|图纸|能量核心|能量晶体|晶核|"
    # 3. 都市高武 / 资产商战 / 文娱
    r"气血丹|气血仪|淬骨膏|精神药剂|凶兽肉|版权|独家版权|股权|股份|定金|违约金|现金|支票|存折|黑卡|豪车|别墅|写字楼|"
    # 4. 女频年代 / 宅斗宫斗 / 世情
    r"粮票|全国粮票|布票|肉票|工业券|油票|工分|地契|房契|田契|铺面|商铺|庄园|宅院|四合院|嫁妆|聘礼|份例|月钱|体己|"
    r"银票|碎银|白银|黄金|银两|铜钱|文钱|云锦|绸缎|首饰|头面|珍珠|金条|"
    # 5. 悬疑怪谈 / 民俗规则
    r"诡器|诡物|规则残片|羊皮纸|蜡烛|寿衣|替死娃娃|镇魂铃|判官笔|绣花鞋|问米碗|封印物|染血的剪刀|阴阳镜|纸人|骨灰盒)"
)

HEURISTIC_NATURAL_PATTERN = re.compile(
    rf'(?P<num>{HEURISTIC_NUMS_REGEX})\s*(?P<unit>{HEURISTIC_UNITS_REGEX})\s*(?P<desc>[一-龥a-zA-Z0-9]{{0,10}}?)(?P<kw>{HEURISTIC_KWS_REGEX})'
)

HEURISTIC_ACQUISITION_VERBS: Set[str] = {
    "收录", "发现", "获得", "激活", "建造", "升级", "开启", "解锁", "掉落", "装备", "制造",
    "打捞", "缴获", "入库", "找到", "运回", "搜刮", "收获", "起出", "搬出", "运送", "加装",
    "清点出", "清点", "囤积", "储备", "开出", "封存着", "拥有", "配备", "装载", "采购",
    "进账", "得到", "采掘", "提炼", "生产", "改装完成", "捕获", "存有", "堆放着", "物资",
    "战利品", "战备", "军械库", "仓库", "掩体", "车间", "补给", "起步", "亮剑", "上线", "改装完成", "完成改装", "总装", "买下", "购置", "兑换", "继承", "分得", "受封", "受赏", "赐予", "赏赐", "炼制", "采摘", "签约", "过户", "划归", "私藏", "缴存", "变现", "到账"
}

HEURISTIC_ENEMY_VERBS: Set[str] = {"击毁", "击沉", "打烂", "摧毁", "炸沉", "包抄", "呼啸而来", "截击", "逼近", "海盗船", "敌方"}

# F06：候选变更方向与裁决取值；消耗动词仅用于预览标注，最终裁决始终由宿主/作者给出。
ASSET_EVENT_DIRECTIONS: Set[str] = {"gain", "consume"}
ASSET_EVENT_DECISIONS: Set[str] = {"accept", "reject"}
HEURISTIC_CONSUMPTION_VERBS: Set[str] = {
    "消耗", "用掉", "用去", "耗费", "花费", "支付", "失去", "损失", "损坏",
    "焚毁", "烧毁", "服用", "吃掉", "扣除", "支出", "耗尽", "报销",
}

class LedgerDirtyError(Exception):
    """防脏写拦截器异常：Markdown 编辑时间晚于 JSON 数据源"""
    pass


@dataclass
class AssetItem:
    """标准资产条目模型"""
    id: str                                                 # 资产唯一标识
    name: str                                               # 资产名称
    category: str                                           # 资产分类（七类资产之一）
    quantity: Union[int, float]                             # 数量
    unit: str                                               # 单位（如 "块", "把", "枚"）
    owner: str = "主角"                                     # 原始所有者（默认 "主角"）
    current_holder: str = ""                                 # 当前实际持有人（默认同 owner）
    status: str = "ACQUIRED"                                # 当前状态
    origin_chapter: float = 1.0                             # 获取章节
    lend_meta: Optional[Dict[str, Any]] = None              # 借出元数据（借用人、时限等）
    constraints: Dict[str, Any] = field(default_factory=dict)  # 约束说明（durability, time_limit, binding_env 等）
    history: List[Dict[str, Any]] = field(default_factory=list)  # 变迁历史流水

    def __post_init__(self) -> None:
        if self.category not in ASSET_CATEGORIES:
            raise ValueError(f"未知资产分类 '{self.category}'，有效分类为: {sorted(ASSET_CATEGORIES)}")
        if self.status not in ASSET_STATUSES:
            raise ValueError(f"未知资产状态 '{self.status}'，有效状态为: {sorted(ASSET_STATUSES)}")
        if not self.current_holder:
            self.current_holder = self.owner

    def transition(
        self,
        new_status: str,
        chapter: float,
        reason: str = "",
        holder: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """执行状态流转并自动记录变迁流水日志"""
        if new_status not in ASSET_STATUSES:
            raise ValueError(f"目标状态 '{new_status}' 不在有效状态集合中: {sorted(ASSET_STATUSES)}")

        old_status = self.status

        log_entry: Dict[str, Any] = {
            "action": "transition",
            "from_status": old_status,
            "to_status": new_status,
            "chapter": chapter,
            "reason": reason,
            "timestamp": time.time(),
        }

        if new_status == "LENT_OUT":
            self.current_holder = holder or self.current_holder
            self.lend_meta = meta or {}
            log_entry["holder"] = self.current_holder
            log_entry["lend_meta"] = self.lend_meta
        elif new_status == "RECLAIMED":
            self.current_holder = self.owner
            self.lend_meta = None
            log_entry["holder"] = self.current_holder
        elif new_status == "TRANSFERRED":
            if holder:
                self.owner = holder
                self.current_holder = holder
                log_entry["holder"] = holder
        else:
            if holder:
                self.current_holder = holder
                log_entry["holder"] = holder

        self.status = new_status
        self.history.append(log_entry)

    def modify_quantity(
        self,
        delta: Union[int, float],
        chapter: float,
        reason: str = "",
    ) -> None:
        """增减资产数量，并在消耗殆尽时自动变迁为 CONSUMED 状态"""
        old_qty = self.quantity
        new_qty = self.quantity + delta
        if new_qty < 0:
            new_qty = 0
        self.quantity = new_qty

        log_entry: Dict[str, Any] = {
            "action": "modify_quantity",
            "delta": delta,
            "from_quantity": old_qty,
            "to_quantity": self.quantity,
            "chapter": chapter,
            "reason": reason,
            "timestamp": time.time(),
        }
        self.history.append(log_entry)

        if self.quantity == 0 and self.category == "丹药耗材":
            self.transition("CONSUMED", chapter=chapter, reason=f"耗尽自动归档: {reason}")

    def to_dict(self) -> Dict[str, Any]:
        """将对象转换为纯字典格式便于 JSON 序列化"""
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "quantity": self.quantity,
            "unit": self.unit,
            "owner": self.owner,
            "current_holder": self.current_holder,
            "status": self.status,
            "origin_chapter": self.origin_chapter,
            "lend_meta": self.lend_meta,
            "constraints": dict(self.constraints),
            "history": list(self.history),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AssetItem":
        """从字典反序列化构建 AssetItem 实例"""
        if not isinstance(d, dict):
            raise ValueError("资产条目必须为对象")
        for key in ("id", "name", "category", "unit", "owner", "current_holder", "status"):
            if key in d and not isinstance(d[key], str):
                raise ValueError(f"资产字段 {key} 必须为字符串")
        quantity = d.get("quantity", 1)
        if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or not math.isfinite(quantity):
            raise ValueError("资产 quantity 必须为有限数值")
        origin_chapter = float(d.get("origin_chapter", 1.0))
        if not math.isfinite(origin_chapter):
            raise ValueError("资产 origin_chapter 必须为有限数值")
        if not isinstance(d.get("constraints", {}), dict):
            raise ValueError("资产 constraints 必须为对象")
        history = d.get("history", [])
        if not isinstance(history, list) or any(not isinstance(item, dict) for item in history):
            raise ValueError("资产 history 必须为对象列表")
        if d.get("lend_meta") is not None and not isinstance(d["lend_meta"], dict):
            raise ValueError("资产 lend_meta 必须为对象或空值")
        return cls(
            id=str(d.get("id", "")),
            name=str(d.get("name", "")),
            category=str(d.get("category", "装备道具")),
            quantity=quantity,
            unit=str(d.get("unit", "个")),
            owner=str(d.get("owner", "主角")),
            current_holder=str(d.get("current_holder", d.get("owner", "主角"))),
            status=str(d.get("status", "ACQUIRED")),
            origin_chapter=origin_chapter,
            lend_meta=d.get("lend_meta"),
            constraints=dict(d.get("constraints", {})),
            history=list(history),
        )


@dataclass
class LedgerState:
    """全量资源账本状态快照"""
    last_updated_chapter: float = 0.0
    assets: Dict[str, AssetItem] = field(default_factory=dict)
    foreshadowing_stash: List[Dict[str, Any]] = field(default_factory=list)
    # F06 可选新键：候选变更的确认/否决裁决流水；旧账本缺少该键时按空值加载。
    asset_events: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典结构"""
        return {
            "last_updated_chapter": self.last_updated_chapter,
            "assets": {k: v.to_dict() for k, v in self.assets.items()},
            "foreshadowing_stash": list(self.foreshadowing_stash),
            "asset_events": [dict(item) for item in self.asset_events],
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LedgerState":
        """从字典反序列化为 LedgerState 实例"""
        if not isinstance(d, dict):
            raise ValueError("账本根节点必须为对象")
        raw_assets = d.get("assets", {})
        if not isinstance(raw_assets, dict):
            raise ValueError("账本 assets 必须为对象")
        stash = d.get("foreshadowing_stash", [])
        if not isinstance(stash, list) or any(not isinstance(item, dict) for item in stash):
            raise ValueError("账本 foreshadowing_stash 必须为对象列表")
        raw_events = d.get("asset_events", [])
        if not isinstance(raw_events, list) or any(not isinstance(item, dict) for item in raw_events):
            raise ValueError("账本 asset_events 必须为对象列表")
        for event in raw_events:
            for text_key in (
                "event_id", "name", "owner", "current_holder", "category", "unit",
                "direction", "evidence", "decision", "source", "reason", "decided_at",
            ):
                if text_key in event and not isinstance(event[text_key], str):
                    raise ValueError(f"资产事件 {text_key} 必须为字符串")
            for num_key in ("chapter", "quantity", "line_number", "column", "quantity_before", "quantity_after"):
                value = event.get(num_key)
                if value is None:
                    continue
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                    raise ValueError(f"资产事件 {num_key} 必须为有限数值或 null")
        for item in stash:
            for text_key in ("name", "origin", "status"):
                if text_key in item and not isinstance(item[text_key], str):
                    raise ValueError(f"伏笔 {text_key} 必须为字符串")
            source_chapter = item.get("source_chapter")
            if source_chapter is not None:
                if isinstance(source_chapter, bool) or not isinstance(source_chapter, (int, float, str)) or not math.isfinite(float(source_chapter)):
                    raise ValueError("伏笔 source_chapter 必须为有限章号或 null")
        assets: Dict[str, AssetItem] = {}
        for k, v in raw_assets.items():
            if isinstance(v, AssetItem):
                assets[k] = v
            elif isinstance(v, dict):
                assets[k] = AssetItem.from_dict(v)
            else:
                raise ValueError(f"资产 {k} 必须为对象")

        return cls(
            last_updated_chapter=float(d.get("last_updated_chapter", 0.0)),
            assets=assets,
            foreshadowing_stash=list(stash),
            asset_events=[dict(item) for item in raw_events],
        )


def parse_chinese_or_arabic_number(s: str) -> Union[int, float]:
    """解析中文或阿拉伯数字字符串为数值（支持万、千、百、亿及小数）"""
    if not s:
        return 1

    s = s.strip()
    try:
        return float(s) if "." in s else int(s)
    except ValueError:
        pass

    if s in ("百", "上百"):
        return 100
    if s in ("千", "上千"):
        return 1000
    if s in ("万", "上万"):
        return 10000

    clean_s = s
    m_num_unit = re.match(r'^(\d+(?:\.\d+)?)([万千百亿])$', clean_s)
    if m_num_unit:
        n_val = float(m_num_unit.group(1)) if "." in m_num_unit.group(1) else int(m_num_unit.group(1))
        mult = {"百": 100, "千": 1000, "万": 10000, "亿": 100000000}[m_num_unit.group(2)]
        return n_val * mult

    for pfx in ("共", "约", "近", "超"):
        if clean_s.startswith(pfx) and len(clean_s) > 1:
            clean_s = clean_s[len(pfx):]
    for sfx in ("余", "多", "来", "只"):
        if clean_s.endswith(sfx) and len(clean_s) > 1:
            clean_s = clean_s[:-len(sfx)]

    cn_digits = {
        "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
        "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    }
    units = {"十": 10, "百": 100, "千": 1000, "万": 10000, "亿": 100000000}

    if clean_s in cn_digits:
        return cn_digits[clean_s]
    if clean_s in units:
        return units[clean_s]

    total = 0
    curr = 0
    for char in clean_s:
        if char in cn_digits:
            curr = cn_digits[char]
        elif char in units:
            unit_val = units[char]
            if curr == 0:
                curr = 1
            if unit_val >= 10000:
                total = (total + curr) * unit_val
            else:
                total += curr * unit_val
            curr = 0
    total += curr
    return total if total > 0 else 1


def _categorize_asset(name: str) -> str:
    """根据资产名称启发式推断全题材标准化资产类别"""
    # 1. 地契房产
    if any(k in name for k in (
        "地契", "房契", "田契", "商铺", "庄园", "宅院", "别院", "四合院",
        "铺面", "房产", "宅基地", "厂房", "别墅", "山头", "果园", "鱼塘", "祖宅", "写字楼"
    )):
        return "地契房产"

    # 2. 规则诡器
    if any(k in name for k in (
        "诡器", "诡物", "规则残片", "替死娃娃", "羊皮纸", "寿衣", "绣花鞋",
        "问米碗", "镇魂铃", "封印物", "骨灰盒", "纸人", "阴阳镜", "判官笔", "生路纸条"
    )):
        return "规则诡器"

    # 3. 身份特权 (身份权限)
    if any(k in name for k in (
        "令牌", "虎符", "密令", "玉牒", "官印", "铭牌", "通行证", "委任状",
        "聘书", "介绍信", "户口簿", "会员卡", "协议", "契约", "股权证书", "合同", "证书"
    )):
        return "身份特权"

    # 4. 资金资产
    if any(k in name for k in (
        "重构点", "点数", "积分", "金币", "银币", "铜币", "灵石", "能量币",
        "晶石", "贡献点", "碎银", "银两", "金条", "黄金", "文钱", "现金",
        "存折", "支票", "黑卡", "股份", "股权", "版权", "定金", "彩礼",
        "嫁妆", "分红", "工分", "两银", "贯钱", "万两"
    )):
        return "资金资产"

    # 5. 丹药耗材
    if any(k in name for k in (
        "丹", "药", "灵药", "灵草", "灵芝", "人参", "雪莲", "兽核", "妖丹", "晶核",
        "米", "粮", "面", "罐头", "肉", "水", "油", "柴油", "重油", "汽油", "煤油",
        "抗生素", "急救包", "绷带", "弹药", "子弹", "炮弹", "深弹", "高爆弹", "穿甲弹",
        "炸药", "防弹钢", "特种钢", "钛合金", "铝合金", "合金", "口粮", "饼干",
        "符箓", "符纸", "粮票", "布票", "肉票", "油票", "工业券", "灵液", "灵泉", "灵髓"
    )):
        return "丹药耗材"

    # 6. 功法神通
    if any(k in name for k in (
        "蓝图", "图纸", "设计图", "功法", "神通", "秘籍", "心法", "技能",
        "剑诀", "拳谱", "身法", "禁术", "残卷", "阵图", "传承", "战法"
    )):
        return "功法神通"

    # 7. 随行战力
    if any(k in name for k in (
        "女兵", "幸存者", "工人", "工程师", "水鬼", "战队", "战友", "部下",
        "亲卫", "随从", "灵兽", "战宠", "死士", "暗卫", "傀儡", "护院", "丫鬟", "家丁", "掌柜", "门客"
    )):
        return "随行战力"

    # 8. 默认为装备道具
    return "装备道具"


def _clean_asset_name(raw: str) -> str:
    """清洗资产名称中的多余助词与标点"""
    name = raw.strip("：: ，,、。！？“”\"'[]【】 ")
    name = re.sub(r'^(?:获得|收录|发现|开启|解锁|掉落|装备|制造|打捞|缴获|入库|分得|继承|买下|采摘|签约|奖励|结算|清点出|清点)[：:\s]*', '', name)
    name = re.sub(r'^(?:未使用的|未经使用的|全新|完好无损的|进口的|德国进口的)', '', name)
    name = re.sub(r'^(?:一枚|一座|一台|一艘|一套|一只|一门|一把|一挺)', '', name)
    return name.strip("：: ，,、。！？“”\"'[]【】 ")


def _iter_heuristic_brackets(text: str) -> Iterator[re.Match]:
    """按右括号划分候选区间，每段只匹配一次，避免重复回溯同一后缀。"""
    opening = text.find("【")
    while opening != -1:
        closing = text.find("】", opening + 1)
        if closing == -1:
            break
        match = HEURISTIC_BRACKET_PATTERN.match(text, opening, closing + 1)
        if match is not None:
            yield match
        opening = text.find("【", closing + 1)


def extract_heuristic_assets(text: str, chapter_index: float, genre: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    启发式资产抽取器：在缺乏人工 audit:stash 注释标签时，
    从自然网文中识别物资、装备出装、军工资产与系统收录物品。
    """
    if not text:
        return []

    raw_candidates: List[Tuple[str, Union[int, float], str, str]] = []

    # 1. 扫描系统出装/提示括号块 【获得/收录/解锁/建造/打捞/缴获...】
    for m in _iter_heuristic_brackets(text):
        full_bracket = m.group(0)
        bracket_inner = full_bracket[1:-1].strip()
        # 优先以冒号切分标题与正文
        if "：" in bracket_inner or ":" in bracket_inner:
            hdr, body = re.split(r'[：:]', bracket_inner, maxsplit=1)
            if re.match(rf'^\d+(?:\.\d+)?[万千百亿]?\s*(?:{HEURISTIC_UNITS_REGEX})$', body.strip()):
                content = f'{hdr.strip()}：{body.strip()}'
            else:
                content = body.strip()
        else:
            content = m.group("content").strip()
        clean_content = re.sub(r'^(?:成功[：:]|物资[：:]|获得[：:]|装备[：:]|制造[：:]|缴获[：:]|发现[：:]|核算[：:]|清点[：:]|奖励[：:]|结算[：:]|分得[：:])', '', content).strip()
        sub_items = [s.strip() for s in re.split(r'[，,、；;\s与和]+', clean_content) if s.strip()]
        for sub in sub_items:
            m_cross = re.match(r'^(?P<name>[^×*x\d]+?)[×*x]\s*(?P<num>\d+(?:\.\d+)?)(?:\s*(?P<unit>[\u4e00-\u9fa5]+))?$', sub)
            if m_cross:
                nm = _clean_asset_name(m_cross.group("name"))
                qty = parse_chinese_or_arabic_number(m_cross.group("num"))
                un = m_cross.group("unit") or ("台" if "机床" in nm else ("份" if "蓝图" in nm else "个"))
                if len(nm) >= 2:
                    raw_candidates.append((nm, qty, un, full_bracket))
                continue

            m_nu = re.match(rf'^(?P<name>.+?)(?<![\d.])(?P<num>\d+(?:\.\d+)?[万千百亿]?|[一二两三四五六七八九十百千万]+)\s*(?P<unit>{HEURISTIC_UNITS_REGEX})$', sub)
            if m_nu:
                nm = _clean_asset_name(m_nu.group("name"))
                qty = parse_chinese_or_arabic_number(m_nu.group("num"))
                un = m_nu.group("unit")
                if len(nm) >= 2:
                    raw_candidates.append((nm, qty, un, full_bracket))
                continue

            m_un = re.match(rf'^(?P<num>\d+|[一二两三四五六七八九十百千万半]+)\s*(?P<unit>{HEURISTIC_UNITS_REGEX})\s*(?P<name>.+)$', sub)
            if m_un:
                nm = _clean_asset_name(m_un.group("name"))
                qty = parse_chinese_or_arabic_number(m_un.group("num"))
                un = m_un.group("unit")
                if len(nm) >= 2:
                    raw_candidates.append((nm, qty, un, full_bracket))
                continue

            nm = _clean_asset_name(sub)
            if len(nm) >= 2 and not any(p in nm for p in ("完成", "就位", "确认", "正在", "开始", "掩体", "仓库", "基地")):
                un = "套" if any(u in nm for u in ("声呐", "雷达", "系统", "网络")) else ("门" if "炮" in nm else "个")
                raw_candidates.append((nm, 1, un, full_bracket))

    # 2. 扫描自然文本中的 数量 + 单位 + 军工物资名称
    for line in text.splitlines():
        clean_l = line.strip()
        if not clean_l:
            continue
        for m in HEURISTIC_NATURAL_PATTERN.finditer(clean_l):
            matched_str = m.group(0)
            num_str = m.group("num")
            unit_str = m.group("unit")
            desc_str = m.group("desc") or ""
            kw_str = m.group("kw")

            full_name = _clean_asset_name(desc_str + kw_str)
            if len(full_name) < 2:
                continue

            sent_context = clean_l
            enemy_factions = {"敌方", "敌军", "敌舰", "敌艇", "海盗", "黑旗帮", "铁钩帮", "水匪", "变异体", "丧尸"}
            loot_verbs = {"缴获", "打捞", "俘获", "搜刮", "战利品", "起出", "入库"}

            # 若名称包含敌对阵营特征且无明确战利品/缴获动词，排除敌方目标
            if any(ef in full_name for ef in enemy_factions) and not any(lv in sent_context for lv in loot_verbs):
                continue

            has_acq = any(v in sent_context for v in HEURISTIC_ACQUISITION_VERBS)
            has_enemy = any(v in sent_context for v in HEURISTIC_ENEMY_VERBS)
            if has_enemy and not any(lv in sent_context for lv in loot_verbs):
                continue

            qty = parse_chinese_or_arabic_number(num_str)
            raw_candidates.append((full_name, qty, unit_str, matched_str))

    # 3. 结果去重与规整
    aggregated: Dict[str, Tuple[Union[int, float], str, str]] = {}
    for name, qty, unit, snip in raw_candidates:
        if name in ("机床", "钢材", "物资", "装备") and any(name in k for k in aggregated.keys() if len(k) > len(name)):
            continue
        if name not in aggregated:
            aggregated[name] = (qty, unit, snip)
        else:
            old_qty, old_un, old_snip = aggregated[name]
            if qty > old_qty:
                aggregated[name] = (qty, unit, snip)

    results: List[Dict[str, Any]] = []
    idx = 1
    for name, (qty, unit, snip) in aggregated.items():
        slug = re.sub(r'[^a-zA-Z0-9一-龥]', '', name)
        asset_id = f"ast_c{int(chapter_index):03d}_{idx}_{slug}"
        category = _categorize_asset(name)
        results.append({
            "id": asset_id,
            "name": name,
            "category": category,
            "quantity": qty,
            "unit": unit,
            "owner": "主角",
            "current_holder": "主角",
            "status": "ACQUIRED",
            "origin_chapter": float(chapter_index),
            "constraints": {},
            "raw_snippet": snip,
        })
        idx += 1

    return results


def categorize_asset(name: str) -> str:
    """公开入口：按名称启发式推断标准资产类别"""
    return _categorize_asset(str(name or ""))


def _event_chapter_token(chapter: Any) -> str:
    try:
        value = float(chapter)
    except (TypeError, ValueError, OverflowError):
        return str(chapter)
    return str(int(value)) if value.is_integer() else f"{value:g}"


def make_asset_event_id(
    chapter: Any,
    line_number: Any,
    column: Any,
    name: str,
    owner: str,
    direction: str,
    quantity: Any,
    unit: str,
) -> str:
    """构造候选变更的稳定身份：章号 + 行列 + 身份 + 方向 + 数量 + 单位。"""
    basis = "|".join(
        [
            _event_chapter_token(chapter),
            str(line_number if line_number is not None else ""),
            str(column if column is not None else ""),
            str(name or "").strip(),
            str(owner or "").strip(),
            str(direction or "").strip(),
            repr(quantity),
            str(unit or "").strip(),
        ]
    )
    return "asset-event-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def make_confirmed_asset_id(name: str, owner: str) -> str:
    """确认新增资产时的确定性身份：名称 + 所有者，避免同名不同所有者互相覆盖。"""
    slug = re.sub(r'[^a-zA-Z0-9一-龥]', '', str(name or ""))[:12] or "asset"
    digest = hashlib.sha256(
        f"{str(name or '').strip()}|{str(owner or '').strip()}".encode("utf-8")
    ).hexdigest()[:8]
    return f"ast_evt_{slug}_{digest}"


def find_asset_by_identity(state: "LedgerState", name: str, owner: str) -> Optional[AssetItem]:
    """按 (名称, 所有者) 定位资产条目；同名不同所有者必须是两条独立记录。"""
    target_name = str(name or "").strip()
    if not target_name:
        return None
    target_owner = str(owner or "").strip()
    for asset_id in sorted(state.assets):
        item = state.assets[asset_id]
        if item.name.strip() == target_name and item.owner.strip() == target_owner:
            return item
    return None


def extract_heuristic_asset_changes(
    text: str,
    chapter_index: float,
    owner: str = "主角",
) -> List[Dict[str, Any]]:
    """按出现次数提取候选资产变更（gain/consume）。

    预览只做启发式标注：数量 + 单位 + 物资名称同现时给出候选，方向由同一小句内的
    消耗动词判定；是否属于真实变更始终由宿主或作者裁决，本函数不会改动账本。
    """
    if not text:
        return []
    owner_value = str(owner or "主角").strip() or "主角"
    results: List[Dict[str, Any]] = []
    enemy_factions = {"敌方", "敌军", "敌舰", "敌艇", "海盗", "黑旗帮", "铁钩帮", "水匪", "变异体", "丧尸"}
    loot_verbs = {"缴获", "打捞", "俘获", "搜刮", "战利品", "起出", "入库"}
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        for match in HEURISTIC_NATURAL_PATTERN.finditer(line):
            name = _clean_asset_name((match.group("desc") or "") + match.group("kw"))
            if len(name) < 2:
                continue
            if any(faction in name for faction in enemy_factions) and not any(
                verb in line for verb in loot_verbs
            ):
                continue
            try:
                quantity = parse_chinese_or_arabic_number(match.group("num"))
            except (ValueError, OverflowError):
                continue
            if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
                continue
            if not math.isfinite(float(quantity)) or quantity <= 0:
                continue
            unit = match.group("unit")
            # 方向判定只看同一小句前缀，避免“先获得后消耗”整行串味。
            segment_start = max(line.rfind(delimiter, 0, match.start()) for delimiter in "，,。！？；;、") + 1
            prefix = line[segment_start:match.start()]
            direction = "consume" if any(verb in prefix for verb in HEURISTIC_CONSUMPTION_VERBS) else "gain"
            results.append({
                "event_id": make_asset_event_id(
                    chapter_index, line_number, match.start() + 1, name, owner_value, direction, quantity, unit
                ),
                "name": name,
                "owner": owner_value,
                "current_holder": owner_value,
                "category": _categorize_asset(name),
                "direction": direction,
                "quantity": quantity,
                "unit": unit,
                "chapter": float(chapter_index),
                "line_number": line_number,
                "column": match.start() + 1,
                "evidence": match.group(0),
                "source": "heuristic",
            })
    return results


def _parse_foreshadowing_content(
    content: str,
    results: List[Dict[str, str]],
    seen: Set[Tuple[str, str, str]],
) -> None:
    """解析单条伏笔/悬念标记内容并去重记录"""
    if not content:
        return

    m_orig = re.search(r'origin="([^"]*)"', content)
    origin = m_orig.group(1).strip() if m_orig else ""

    m_stat = re.search(r'status="([^"]*)"', content)
    status = m_stat.group(1).strip() if m_stat else ""

    m_name = re.search(r'name="([^"]+)"', content)
    if m_name:
        name = m_name.group(1).strip()
    else:
        clean = re.sub(r'(?:origin|status)="[^"]*"', '', content).strip()
        parts = [p.strip() for p in re.split(r'[|，,；;]', clean) if p.strip()]
        if not parts:
            return
        name = parts[0]
        for p in parts[1:]:
            mo = re.match(r'^(?:来源|出处|章节)[：:]\s*(.+)$', p)
            if mo:
                origin = mo.group(1).strip()
                continue
            ms = re.match(r'^(?:状态)[：:]\s*(.+)$', p)
            if ms:
                status = ms.group(1).strip()
                continue
            if re.match(r'^第?\d+章$', p):
                origin = p
                continue
            if p in ("未解", "未回收", "已揭开", "UNACQUIRED", "STASH", "ACQUIRED", "PENDING"):
                status = p
                continue

    name = name.strip("：: ，,、。！？“”\"\'[]【】 ")
    if not name:
        return

    key = (name, origin, status)
    if key not in seen:
        seen.add(key)
        results.append({"name": name, "origin": origin, "status": status})


def scan_foreshadowing_tags(text: str) -> List[Dict[str, str]]:
    """扫描提取文本中的伏笔缓冲池注释标签与悬念标记。

    支持语法：
    1. 标准 HTML 注释标签：<!-- audit:stash name="..." [origin="..."] [status="..."] -->
    2. 中文 HTML 注释标签：<!-- 伏笔:... --> / <!-- 悬念:... -->
    3. 方括号与六角括号标记：【伏笔:...】 / 【悬念:...】 / [伏笔:...] / (伏笔:...)
    """
    if not text:
        return []

    results: List[Dict[str, str]] = []
    seen: Set[Tuple[str, str, str]] = set()

    # 1. 扫描标准 HTML 注释标签
    pattern_html = re.compile(
        r'<!--\s*audit:stash\s+name="(?P<name>[^"]+)"(?:\s+origin="(?P<origin>[^"]*)")?(?:\s+status="(?P<status>[^"]*)")?\s*-->',
        re.DOTALL,
    )
    for match in pattern_html.finditer(text):
        name = match.group("name").strip()
        origin = (match.group("origin") or "").strip()
        status = (match.group("status") or "").strip()
        key = (name, origin, status)
        if key not in seen:
            seen.add(key)
            results.append({"name": name, "origin": origin, "status": status})

    # 2. 扫描中文 HTML 注释标签 <!-- 伏笔:... -->
    pattern_cn_html = re.compile(r'<!--\s*(?:audit:stash:)?(?:伏笔|悬念|线索|暗线)\s*[：:]\s*(?P<content>.*?)\s*-->', re.DOTALL)
    for match in pattern_cn_html.finditer(text):
        content = match.group("content").strip()
        _parse_foreshadowing_content(content, results, seen)

    # 3. 扫描文本括号标记 【伏笔:...】 / [伏笔:...] / (伏笔:...)
    pattern_bracket = re.compile(r'[【\[（\(](?:伏笔|悬念|线索|暗线|待填坑)\s*[：:]\s*(?P<content>[^】\]）\)]+)[】\]）\)]')
    for match in pattern_bracket.finditer(text):
        content = match.group("content").strip()
        _parse_foreshadowing_content(content, results, seen)

    return results


def _ledger_recovery_path(json_path: Path) -> Path:
    return json_path.with_name(f".{json_path.name}.recovery.json")


def ensure_ledger_recovered(json_path: Path) -> None:
    """未完成的双轨保存必须先恢复，force 不能绕过恢复标记。"""
    recovery_path = _ledger_recovery_path(Path(json_path))
    if recovery_path.exists():
        raise SafeIOWriteError(
            f"账本存在未完成的双轨保存，请先按恢复标记 {recovery_path} "
            "检查并恢复原文件，再移除该标记；恢复副本不得直接删除。"
        )


def check_dirty_state(md_path: Path, json_path: Path, tolerance: float = 0.05) -> bool:
    """检查 Markdown 账本是否存在比 JSON 更加新的外部修改冲突

    存在双轨恢复标记，或两轨均存在且 Markdown 时间戳明显更新时返回 True。
    增加 0.05s 时间戳浮点安全容差，避免 Windows NTFS 微秒截断引起误判脏写。
    """
    md = Path(md_path)
    js = Path(json_path)
    if _ledger_recovery_path(js).exists():
        return True
    if not md.is_file() or not js.is_file():
        return False
    return (md.stat().st_mtime - js.stat().st_mtime) > tolerance


def _format_constraints(constraints: Dict[str, Any]) -> str:
    """格式化约束条件字段为紧凑字符串"""
    if not constraints:
        return "-"
    parts = []
    for k, v in constraints.items():
        parts.append(f"{k}: {v}")
    return "; ".join(parts)


def _parse_constraints(c_str: str) -> Dict[str, Any]:
    """从字符串反向解析约束字典"""
    if not c_str or c_str.strip() == "-":
        return {}
    res: Dict[str, Any] = {}
    items = c_str.split(";")
    for item in items:
        if ":" in item:
            k, v = item.split(":", 1)
            k_clean = k.strip()
            v_clean = v.strip()
            if v_clean.isdigit():
                res[k_clean] = int(v_clean)
            else:
                try:
                    res[k_clean] = float(v_clean)
                except ValueError:
                    res[k_clean] = v_clean
    return res


def render_ledger_markdown(state: LedgerState) -> str:
    """冷热资产分层渲染 Markdown 账本文档

    - 热资产（状态为 EQUIPPED, ACQUIRED, LENT_OUT 且数量 > 0）：顶层表格清晰直观；
    - 冷资产（状态为 CONSUMED, DAMAGED, TRANSFERRED 或数量 <= 0）：折叠在 details 标签中；
    - 伏笔待回收池：在末尾以折叠区块展示。
    """
    lines: List[str] = [
        f"# 资源账本（截至第 {state.last_updated_chapter} 章）",
        "",
        "## 当前持有与生效资产（热资产）",
        "",
    ]

    hot_assets: List[AssetItem] = []
    cold_assets: List[AssetItem] = []

    for item in state.assets.values():
        if item.quantity <= 0 or item.status in {"CONSUMED", "DAMAGED", "TRANSFERRED"}:
            cold_assets.append(item)
        elif item.status in {"EQUIPPED", "ACQUIRED", "LENT_OUT", "RECLAIMED", "RESTORED"}:
            hot_assets.append(item)
        else:
            cold_assets.append(item)

    table_header = "| 资产ID | 资产名称 | 类别 | 数量 | 单位 | 所有者 | 当前持有者 | 状态 | 初始章节 | 约束说明 |"
    table_sep = "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |"

    def format_row(it: AssetItem) -> str:
        c_desc = _format_constraints(it.constraints)
        return (
            f"| {it.id} | {it.name} | {it.category} | {it.quantity} | {it.unit} | "
            f"{it.owner} | {it.current_holder} | {it.status} | {it.origin_chapter} | {c_desc} |"
        )

    if hot_assets:
        lines.append(table_header)
        lines.append(table_sep)
        for item in hot_assets:
            lines.append(format_row(item))
    else:
        lines.append("（暂无活跃资产）")

    lines.append("")
    lines.append("<details>")
    lines.append("<summary>历史已消耗与归档资产</summary>")
    lines.append("")

    if cold_assets:
        lines.append(table_header)
        lines.append(table_sep)
        for item in cold_assets:
            lines.append(format_row(item))
    else:
        lines.append("（暂无归档资产）")

    lines.append("")
    lines.append("</details>")

    if state.foreshadowing_stash:
        lines.append("")
        lines.append("<details>")
        lines.append("<summary>伏笔待回收池</summary>")
        lines.append("")
        lines.append("| 伏笔名称 | 来源线索 | 初始状态 |")
        lines.append("| :--- | :--- | :--- |")
        for stash in state.foreshadowing_stash:
            s_name = stash.get("name", "")
            s_origin = stash.get("origin", "-") or "-"
            s_status = stash.get("status", "-") or "-"
            lines.append(f"| {s_name} | {s_origin} | {s_status} |")
        lines.append("")
        lines.append("</details>")

    lines.append("")
    return "\n".join(lines)


def save_ledger_state(
    state: LedgerState,
    json_path: Path,
    md_path: Optional[Path] = None,
    force: bool = False,
) -> None:
    """保存账本；双轨提交失败时恢复原字节，恢复失败则保留副本并阻止复用。"""
    json_p = Path(json_path)
    md_p = Path(md_path) if md_path else None
    ensure_ledger_recovered(json_p)

    # 防脏写拦截
    if md_p is not None and not force:
        if check_dirty_state(md_p, json_p):
            raise LedgerDirtyError(
                f"检测到 Markdown 账本 ({md_p}) 修改时间晚于 JSON 数据源 ({json_p})，"
                "存在潜在外部人工编辑冲突！若需强制覆写请指定 force=True，或先执行 sync_from_markdown。"
            )

    # 两份内容必须均准备成功后，才能改变任意目标文件。
    json_content = json.dumps(state.to_dict(), ensure_ascii=False, indent=2)
    if md_p is None:
        write_file_safe(json_p, json_content)
        return
    md_content = render_ledger_markdown(state)
    if json_p.resolve() == md_p.resolve():
        raise SafeIOWriteError("JSON 与 Markdown 账本不能使用同一路径")

    recovery_path = _ledger_recovery_path(json_p)
    backups: Dict[Path, Optional[Path]] = {}
    attempted: List[Path] = []
    created_dirs: List[Path] = []
    owns_marker = False
    completed = False
    try:
        for target in (json_p, md_p):
            missing_dirs: List[Path] = []
            parent = target.parent
            while not parent.exists():
                missing_dirs.append(parent)
                parent = parent.parent
            created_dirs.extend(path for path in reversed(missing_dirs) if path not in created_dirs)
            target.parent.mkdir(parents=True, exist_ok=True)

        # 独占标记同时阻止另一次保存覆盖本次恢复信息。
        with recovery_path.open("x", encoding="utf-8") as marker:
            owns_marker = True
            json.dump({"phase": "preparing", "targets": [str(json_p.resolve()), str(md_p.resolve())]}, marker, ensure_ascii=False)
            marker.flush()
            os.fsync(marker.fileno())

        for target in (json_p, md_p):
            backups[target] = None
            if target.exists():
                original_stat = target.stat()
                backup_fd, backup_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.recovery-", suffix=".bak")
                backup_path = Path(backup_name)
                backups[target] = backup_path
                with os.fdopen(backup_fd, "wb") as backup:
                    backup.write(target.read_bytes())
                    backup.flush()
                    os.fsync(backup.fileno())
                os.utime(backup_path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

        write_file_safe(recovery_path, json.dumps({
            "phase": "committing",
            "targets": [
                {"path": str(target.resolve()), "existed": backup is not None,
                 "backup": str(backup.resolve()) if backup is not None else None}
                for target, backup in backups.items()
            ],
        }, ensure_ascii=False, indent=2))

        for target, content in ((json_p, json_content), (md_p, md_content)):
            attempted.append(target)
            write_file_safe(target, content)

        # 保持原有防脏写时间戳约定；该步骤失败也应恢复两轨。
        md_mtime_ns = md_p.stat().st_mtime_ns
        json_stat = json_p.stat()
        if md_mtime_ns > json_stat.st_mtime_ns:
            os.utime(json_p, ns=(json_stat.st_atime_ns, md_mtime_ns))
        recovery_path.unlink()
        completed = True
    except Exception as error:
        restore_errors: List[str] = []
        for target in reversed(attempted):
            try:
                backup = backups[target]
                if backup is not None:
                    os.replace(backup, target)
                elif target.exists():
                    target.unlink()
            except OSError as restore_error:
                restore_errors.append(f"{target}: {restore_error}")
        if owns_marker and not restore_errors:
            try:
                recovery_path.unlink()
            except OSError as marker_error:
                restore_errors.append(str(marker_error))
        if restore_errors:
            raise SafeIOWriteError(
                f"双轨账本保存失败且恢复未完成，请保留恢复标记 {recovery_path} 与副本："
                + "; ".join(restore_errors)
            ) from error
        raise SafeIOWriteError(f"双轨账本保存失败，原文件已保留或恢复：{error}") from error
    finally:
        # 恢复中断时副本必须留下；正常完成或完整回滚后只清理本次临时文件。
        if owns_marker and not recovery_path.exists():
            for backup in backups.values():
                if backup is not None:
                    try:
                        backup.unlink()
                    except OSError:
                        pass
        if not completed:
            for directory in reversed(created_dirs):
                try:
                    directory.rmdir()
                except OSError:
                    pass


def create_volume_checkpoint(volume: int, state: LedgerState, archive_dir: Path) -> Path:
    """创建分卷封账快照，将当前全量状态归档至 volume_{volume:02d}_ledger.json"""
    arch_dir = Path(archive_dir)
    arch_dir.mkdir(parents=True, exist_ok=True)

    archive_filename = f"volume_{volume:02d}_ledger.json"
    archive_path = arch_dir / archive_filename

    json_content = json.dumps(state.to_dict(), ensure_ascii=False, indent=2)
    write_file_safe(archive_path, json_content)
    return archive_path


def sync_from_markdown(md_path: Path, json_path: Path) -> LedgerState:
    """从 Markdown 账本表格反向增量解析并合并至 JSON 数据源"""
    md_p = Path(md_path)
    json_p = Path(json_path)
    ensure_ledger_recovered(json_p)

    md_content, _, _ = read_file_safe(md_p)

    # 读取现有 JSON 状态或初始化空状态
    if json_p.exists():
        raw_json_str, _, _ = read_file_safe(json_p)
        raw_data = json.loads(raw_json_str)
        state = LedgerState.from_dict(raw_data)
    else:
        state = LedgerState()

    # 解析标题中的章节信息
    chap_match = re.search(r'#\s*资源账本[（\(]截至第\s*([\d\.]+)\s*章[）\)]', md_content)
    if chap_match:
        try:
            state.last_updated_chapter = float(chap_match.group(1))
        except ValueError:
            pass

    # 先完整解析资产表，坏行不能被当作作者主动删除的资产。
    col_mapping: Optional[Dict[str, int]] = None
    column_count = 0
    saw_asset_table = False
    in_asset_section = False
    valid_asset_ids: Set[str] = set()
    required_columns = {"资产ID", "资产名称", "类别", "数量", "单位", "所有者", "当前持有者", "状态", "初始章节"}

    for line_number, raw_line in enumerate(md_content.splitlines(), 1):
        line = raw_line.strip()
        if line.startswith("#") or line.startswith("<summary>") or line == "</details>":
            in_asset_section = "当前持有与生效资产" in line or "历史已消耗与归档资产" in line
        # 先识别畸形表头，避免丢失第一列后把整张表当作普通文本跳过。
        header_cells = [cell.strip() for cell in line.strip("|").split("|")]
        looks_like_header = "资产ID" in header_cells or "资产名称" in header_cells or ("名称" in header_cells and "数量" in header_cells)
        if not line.startswith("|"):
            if "|" in line and (col_mapping is not None or in_asset_section or looks_like_header):
                raise ValueError(f"Markdown 资产表第 {line_number} 行缺少起始分隔符")
            col_mapping = None
            continue
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if not cells:
            if col_mapping is not None:
                raise ValueError(f"Markdown 资产表第 {line_number} 行缺少数据列")
            continue

        # 识别表头行
        if looks_like_header:
            col_mapping = {col: idx for idx, col in enumerate(cells)}
            if "名称" in col_mapping and "资产名称" not in col_mapping:
                col_mapping["资产名称"] = col_mapping["名称"]
            if not line.endswith("|") or not required_columns.issubset(col_mapping) or len(set(cells)) != len(cells):
                raise ValueError(f"Markdown 资产表第 {line_number} 行表头缺列或含重复列")
            column_count = len(cells)
            saw_asset_table = True
            continue

        if col_mapping is None and in_asset_section:
            raise ValueError(f"Markdown 资产表第 {line_number} 行缺少有效表头")

        # 跳过分隔行
        if all(re.match(r'^:?-+:?$', c) for c in cells):
            continue

        if col_mapping is None or "资产ID" not in col_mapping:
            continue

        try:
            if not line.endswith("|") or len(cells) != column_count:
                raise ValueError("数据列数与表头不一致")
            item_id = cells[col_mapping["资产ID"]]
            if not item_id:
                raise ValueError("资产ID不能为空")
            if item_id in valid_asset_ids:
                raise ValueError(f"资产ID重复: {item_id}")

            name = cells[col_mapping.get("资产名称", 1)]
            category = cells[col_mapping.get("类别", 2)]
            qty_raw = cells[col_mapping.get("数量", 3)]
            unit = cells[col_mapping.get("单位", 4)]
            owner = cells[col_mapping.get("所有者", 5)]
            current_holder = cells[col_mapping.get("当前持有者", 6)]
            status = cells[col_mapping.get("状态", 7)]
            chap_raw = cells[col_mapping.get("初始章节", 8)]
            constraints_str = cells[col_mapping.get("约束说明", 9)] if "约束说明" in col_mapping else ""

            try:
                quantity = int(qty_raw)
            except ValueError:
                quantity = float(qty_raw)
            origin_chapter = float(chap_raw)
            if (isinstance(quantity, float) and not math.isfinite(quantity)) or not math.isfinite(origin_chapter):
                raise ValueError("数量与初始章节必须为有限数值")
            if isinstance(quantity, float) and quantity.is_integer():
                quantity = int(quantity)
            if category not in ASSET_CATEGORIES or status not in ASSET_STATUSES:
                raise ValueError("资产类别或状态无效")
            if not all((name, unit, owner, current_holder)):
                raise ValueError("资产名称、单位、所有者与当前持有者不能为空")

            if constraints_str and constraints_str.strip() not in ("-", "无"):
                for part in constraints_str.split(";"):
                    if part.strip() and (":" not in part or not part.split(":", 1)[0].strip()):
                        raise ValueError("约束说明必须采用键值对格式")
            constraints = _parse_constraints(constraints_str)

            if item_id in state.assets:
                # 增量更新已有条目
                existing = state.assets[item_id]
                changed = (
                    existing.name != name
                    or existing.category != category
                    or existing.quantity != quantity
                    or existing.unit != unit
                    or existing.owner != owner
                    or existing.current_holder != current_holder
                    or existing.status != status
                    or existing.origin_chapter != origin_chapter
                    or existing.constraints != constraints
                )
                if changed:
                    existing.name = name
                    existing.category = category
                    existing.quantity = quantity
                    existing.unit = unit
                    existing.owner = owner
                    existing.current_holder = current_holder
                    existing.status = status
                    existing.origin_chapter = origin_chapter
                    existing.constraints = constraints
                    existing.history.append({
                        "action": "sync_from_markdown",
                        "timestamp": time.time(),
                    })
            else:
                # 新增条目
                new_item = AssetItem(
                    id=item_id,
                    name=name,
                    category=category,
                    quantity=quantity,
                    unit=unit,
                    owner=owner,
                    current_holder=current_holder,
                    status=status,
                    origin_chapter=origin_chapter,
                    constraints=constraints,
                    history=[{"action": "created_from_markdown_sync", "timestamp": time.time()}],
                )
                state.assets[item_id] = new_item
            valid_asset_ids.add(item_id)
        except (IndexError, ValueError) as e:
            raise ValueError(f"Markdown 资产表第 {line_number} 行无效: {e}") from e

    if not saw_asset_table and (state.assets or "（暂无活跃资产）" not in md_content):
        raise ValueError("Markdown 中未发现可同步的有效资产表")

    # 若成功识别到资产表头，对在 Markdown 中物理删除的条目从 state.assets 中同步清理
    # 安全保护：如果识别到的有效资产集合不为空，且条目不属于冷资产（CONSUMED/DAMAGED/TRANSFERRED 等），才执行清理；
    # 坚决防止 Markdown 仅展示活跃随身资产或发生空表时将冷资产历史记录一笔抹除！
    if saw_asset_table and valid_asset_ids:
        removed_ids = [
            aid for aid, item in list(state.assets.items())
            if aid not in valid_asset_ids and item.status not in COLD_ASSET_STATUSES
        ]
        for aid in removed_ids:
            del state.assets[aid]

    # 持久化回 JSON 并消除 dirty 状态
    save_ledger_state(state, json_p, md_p, force=True)
    return state
