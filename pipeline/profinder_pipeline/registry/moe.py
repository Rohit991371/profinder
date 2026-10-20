from __future__ import annotations

import csv
import re
from pathlib import Path

from .models import RegistryRecord

PROVINCES_ZH_EN = {
    "北京市": "Beijing", "天津市": "Tianjin", "河北省": "Hebei", "山西省": "Shanxi", "内蒙古自治区": "Inner Mongolia",
    "辽宁省": "Liaoning", "吉林省": "Jilin", "黑龙江省": "Heilongjiang", "上海市": "Shanghai", "江苏省": "Jiangsu",
    "浙江省": "Zhejiang", "安徽省": "Anhui", "福建省": "Fujian", "江西省": "Jiangxi", "山东省": "Shandong",
    "河南省": "Henan", "湖北省": "Hubei", "湖南省": "Hunan", "广东省": "Guangdong", "广西壮族自治区": "Guangxi",
    "海南省": "Hainan", "重庆市": "Chongqing", "四川省": "Sichuan", "贵州省": "Guizhou", "云南省": "Yunnan",
    "西藏自治区": "Tibet", "陕西省": "Shaanxi", "甘肃省": "Gansu", "青海省": "Qinghai", "宁夏回族自治区": "Ningxia",
    "新疆维吾尔自治区": "Xinjiang",
}
_HEADER = re.compile(r"^(.+?)（\d+所）$")
# MOE section headers are the ADMINISTERING province; a school located in a municipality belongs to that municipality
# (e.g. 河北工业大学 is listed under 河北省 but its 所在地 is 天津市).
_MUNICIPALITIES = {"北京市": "Beijing", "天津市": "Tianjin", "上海市": "Shanghai", "重庆市": "Chongqing"}


def _cell(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _rows(path: Path) -> list[list[str]]:
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as f:
            return [[_cell(c) for c in r] for r in csv.reader(f)]
    try:
        import xlrd  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise SystemExit("Reading .xls needs 'xlrd' (pip install xlrd). Or save the sheet as UTF-8 CSV.") from e
    sheet = xlrd.open_workbook(str(path)).sheet_by_index(0)
    return [[_cell(c) for c in sheet.row_values(i)] for i in range(sheet.nrows)]


def load_moe(path: str | Path) -> list[RegistryRecord]:
    """MOE national list: columns 序号, 学校名称, 学校标识码, 主管部门, 所在地, 办学层次, 备注; province = header rows."""
    out: list[RegistryRecord] = []
    province_zh: str | None = None
    for r in _rows(Path(path)):
        r = (r + [""] * 7)[:7]
        m = _HEADER.match(r[0])
        if m and not any(r[1:]):
            province_zh = m.group(1)
            continue
        if not r[0].isdigit() or not r[1]:
            continue
        muni = _MUNICIPALITIES.get(r[4])
        out.append(
            RegistryRecord(
                source="moe", external_id=r[2] or None, name_zh=r[1], province_zh=r[4] if muni else province_zh,
                province=muni or PROVINCES_ZH_EN.get(province_zh or ""), city_zh=r[4] or None, country="CN",
                note="|".join(x for x in (r[5], r[6]) if x) or None,
            )
        )
    return out


def index_by_name(records: list[RegistryRecord]) -> dict[str, list[RegistryRecord]]:
    idx: dict[str, list[RegistryRecord]] = {}
    for r in records:
        idx.setdefault(r.name_zh or "", []).append(r)
    return idx