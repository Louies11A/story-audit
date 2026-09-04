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

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from scripts.safe_io import read_file_safe, write_file_safe

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
    current_holder: str = "主角"                             # 当前实际持有人（默认同 owner）
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
        return cls(
            id=str(d.get("id", "")),
            name=str(d.get("name", "")),
            category=str(d.get("category", "装备道具")),
            quantity=d.get("quantity", 1),
            unit=str(d.get("unit", "个")),
            owner=str(d.get("owner", "主角")),
            current_holder=str(d.get("current_holder", d.get("owner", "主角"))),
            status=str(d.get("status", "ACQUIRED")),
            origin_chapter=float(d.get("origin_chapter", 1.0)),
            lend_meta=d.get("lend_meta"),
            constraints=dict(d.get("constraints", {})),
            history=list(d.get("history", [])),
        )


@dataclass
class LedgerState:
    """全量资源账本状态快照"""
    last_updated_chapter: float = 0.0
    assets: Dict[str, AssetItem] = field(default_factory=dict)
    foreshadowing_stash: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典结构"""
        return {
            "last_updated_chapter": self.last_updated_chapter,
            "assets": {k: v.to_dict() for k, v in self.assets.items()},
            "foreshadowing_stash": list(self.foreshadowing_stash),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LedgerState":
        """从字典反序列化为 LedgerState 实例"""
        raw_assets = d.get("assets", {})
        assets: Dict[str, AssetItem] = {}
        for k, v in raw_assets.items():
            if isinstance(v, AssetItem):
                assets[k] = v
            elif isinstance(v, dict):
                assets[k] = AssetItem.from_dict(v)

        return cls(
            last_updated_chapter=float(d.get("last_updated_chapter", 0.0)),
            assets=assets,
            foreshadowing_stash=list(d.get("foreshadowing_stash", [])),
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


def extract_heuristic_assets(text: str, chapter_index: float, genre: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    启发式资产抽取器：在缺乏人工 audit:stash 注释标签时，
    从自然网文中识别物资、装备出装、军工资产与系统收录物品。
    """
    if not text:
        return []

    raw_candidates: List[Tuple[str, Union[int, float], str, str]] = []

    # 1. 扫描系统出装/提示括号块 【获得/收录/解锁/建造/打捞/缴获...】
    for m in HEURISTIC_BRACKET_PATTERN.finditer(text):
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


def check_dirty_state(md_path: Path, json_path: Path, tolerance: float = 0.05) -> bool:
    """检查 Markdown 账本是否存在比 JSON 更加新的外部修改冲突

    当且仅当 md_path 与 json_path 均存在且 (md_path.stat().st_mtime - json_path.stat().st_mtime) > tolerance 时返回 True。
    增加 0.05s 时间戳浮点安全容差，避免 Windows NTFS 微秒截断引起误判脏写。
    """
    md = Path(md_path)
    js = Path(json_path)
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
    """原子保存账本状态并执行防脏写拦截

    若提供了 md_path，在写入前检查 check_dirty_state：
    若 dirty 且 force=False，抛出 LedgerDirtyError；
    写入完成后同步时间戳，消除误报。
    """
    json_p = Path(json_path)
    md_p = Path(md_path) if md_path else None

    # 防脏写拦截
    if md_p is not None and not force:
        if check_dirty_state(md_p, json_p):
            raise LedgerDirtyError(
                f"检测到 Markdown 账本 ({md_p}) 修改时间晚于 JSON 数据源 ({json_p})，"
                "存在潜在外部人工编辑冲突！若需强制覆写请指定 force=True，或先执行 sync_from_markdown。"
            )

    # 保存 JSON
    json_content = json.dumps(state.to_dict(), ensure_ascii=False, indent=2)
    write_file_safe(json_p, json_content)

    # 若指定了 md_path，渲染并原子写入 Markdown
    if md_p is not None:
        md_content = render_ledger_markdown(state)
        write_file_safe(md_p, md_content)

        # 消除时间戳微小偏差带来的脏写误报：使 json 的 mtime 不早于 md 的 mtime
        if md_p.exists() and json_p.exists():
            md_mtime = md_p.stat().st_mtime
            json_mtime = json_p.stat().st_mtime
            if md_mtime > json_mtime:
                os.utime(json_p, (md_mtime, md_mtime))


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

    md_content, _, _ = read_file_safe(md_p)

    # 读取现有 JSON 状态或初始化空状态
    if json_p.is_file():
        raw_json_str, _, _ = read_file_safe(json_p)
        try:
            raw_data = json.loads(raw_json_str)
            state = LedgerState.from_dict(raw_data)
        except Exception:
            state = LedgerState()
    else:
        state = LedgerState()

    # 解析标题中的章节信息
    chap_match = re.search(r'#\s*资源账本[（\(]截至第\s*([\d\.]+)\s*章[）\)]', md_content)
    if chap_match:
        try:
            state.last_updated_chapter = float(chap_match.group(1))
        except ValueError:
            pass

    # 解析表格行
    table_lines = [line.strip() for line in md_content.split(chr(10)) if line.strip().startswith("|")]
    col_mapping: Optional[Dict[str, int]] = None
    valid_asset_ids: Set[str] = set()

    for line in table_lines:
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if not cells:
            continue

        # 识别表头行
        if "资产ID" in cells and ("资产名称" in cells or "名称" in cells):
            col_mapping = {col: idx for idx, col in enumerate(cells)}
            if "名称" in col_mapping and "资产名称" not in col_mapping:
                col_mapping["资产名称"] = col_mapping["名称"]
            continue

        # 跳过分隔行
        if all(re.match(r'^:?-+:?$', c) for c in cells):
            continue

        if col_mapping is None or "资产ID" not in col_mapping:
            continue

        try:
            item_id = cells[col_mapping["资产ID"]]
            if not item_id or item_id.startswith("---"):
                continue

            name = cells[col_mapping.get("资产名称", 1)]
            category = cells[col_mapping.get("类别", 2)]
            qty_raw = cells[col_mapping.get("数量", 3)]
            unit = cells[col_mapping.get("单位", 4)]
            owner = cells[col_mapping.get("所有者", 5)]
            current_holder = cells[col_mapping.get("当前持有者", 6)]
            status = cells[col_mapping.get("状态", 7)]
            chap_raw = cells[col_mapping.get("初始章节", 8)]
            constraints_str = cells[col_mapping.get("约束说明", 9)] if "约束说明" in col_mapping else ""

            # 解析数量
            try:
                quantity = int(qty_raw) if "." not in qty_raw else float(qty_raw)
            except ValueError:
                quantity = 1

            # 解析章节
            try:
                origin_chapter = float(chap_raw)
            except ValueError:
                origin_chapter = 1.0

            constraints = _parse_constraints(constraints_str)

            if item_id in state.assets:
                # 增量更新已有条目
                existing = state.assets[item_id]
                changed = (
                    existing.name != name
                    or existing.quantity != quantity
                    or existing.current_holder != current_holder
                    or existing.status != status
                    or existing.constraints != constraints
                )
                if changed:
                    existing.name = name
                    existing.category = category if category in ASSET_CATEGORIES else existing.category
                    existing.quantity = quantity
                    existing.unit = unit
                    existing.owner = owner
                    existing.current_holder = current_holder
                    existing.status = status if status in ASSET_STATUSES else existing.status
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
                    category=category if category in ASSET_CATEGORIES else "装备道具",
                    quantity=quantity,
                    unit=unit,
                    owner=owner or "主角",
                    current_holder=current_holder or owner or "主角",
                    status=status if status in ASSET_STATUSES else "ACQUIRED",
                    origin_chapter=origin_chapter,
                    constraints=constraints,
                    history=[{"action": "created_from_markdown_sync", "timestamp": time.time()}],
                )
                state.assets[item_id] = new_item
            valid_asset_ids.add(item_id)
        except (IndexError, ValueError):
            continue

    # 若成功识别到资产表头，对在 Markdown 中物理删除的条目从 state.assets 中同步清理
    # 安全保护：如果识别到的有效资产集合不为空，且条目不属于冷资产（CONSUMED/DAMAGED/TRANSFERRED 等），才执行清理；
    # 坚决防止 Markdown 仅展示活跃随身资产或发生空表时将冷资产历史记录一笔抹除！
    if col_mapping is not None and valid_asset_ids:
        removed_ids = [
            aid for aid, item in list(state.assets.items())
            if aid not in valid_asset_ids and item.status not in COLD_ASSET_STATUSES
        ]
        for aid in removed_ids:
            del state.assets[aid]

    # 持久化回 JSON 并消除 dirty 状态
    save_ledger_state(state, json_p, md_p, force=True)
    return state