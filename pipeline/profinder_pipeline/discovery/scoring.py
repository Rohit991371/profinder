from __future__ import annotations

# (keyword, weight). Matched case-insensitively on anchor text; URL hints add +2 each (max 2 hints).
KEYWORDS: dict[str, list[tuple[str, int]]] = {
    "faculty_list": [("师资队伍", 6), ("教师队伍", 6), ("教师名录", 6), ("师资力量", 5), ("全职教师", 5), ("师资", 4), ("教师", 2), ("教授", 2),
                     ("faculty", 4), ("people", 2), ("staff", 2), ("directory", 2)],
    "department": [("计算机科学与技术", 6), ("计算机学院", 6), ("计算机", 5), ("computer science", 6), ("computer", 3), ("软件学院", 4), ("software", 3),
                   ("人工智能", 3), ("网络空间安全", 3), ("cyber", 2), ("信息学院", 2), ("information", 1)],
    "dept_index": [("机构设置", 6), ("院系设置", 6), ("学院设置", 6), ("教学单位", 6), ("学院一览", 5), ("院系导航", 5), ("二级学院", 5), ("学部院系", 5),
                   ("院系", 4), ("院部", 3), ("学部", 3), ("学院", 2), ("schools", 3), ("departments", 3), ("colleges", 3), ("academics", 2)],
    "school_faculty": [("学院师资", 6), ("院系师资", 6), ("各学院师资", 6), ("学院教师", 5), ("faculty by school", 5), ("院系", 3), ("学院", 2), ("schools", 2)],
    "admissions": [("international admissions", 6), ("international students", 5), ("来华留学", 6), ("留学生招生", 6), ("admissions", 4), ("admission", 4),
                   ("留学生", 4), ("国际学生", 4), ("国际教育", 3), ("study in china", 3), ("研究生招生", 3), ("招生", 3), ("apply", 2), ("international", 1)],
    "scholarship": [("chinese government scholarship", 6), ("中国政府奖学金", 6), ("scholarships", 4), ("scholarship", 4), ("奖学金", 4), ("csc", 3),
                    ("financial aid", 2), ("fellowship", 2), ("资助", 2)],
}
URL_HINTS: dict[str, tuple[str, ...]] = {
    "faculty_list": ("faculty", "teacher", "szdw", "jzgml", "szzk", "people", "staff", "shizi", "jsml"),
    "department": ("//cs.", "//cse.", "//scs.", "//sse.", "computer", "jsj"),
    "dept_index": ("yxsz", "jgsz", "academics", "schools", "departments", "yuanxi"),
    "school_faculty": ("xysz", "yxsz", "xyjs", "college"),
    "admissions": ("admission", "apply", "zhaosheng", "lxs", "international", "study"),
    "scholarship": ("scholarship", "jxj", "csc"),
}
NEGATIVE: dict[str, list[tuple[str, int]]] = {
    "faculty_list": [("招聘", 6), ("recruit", 6), ("hiring", 6), ("vacanc", 6), ("招贤", 6), ("alumni", 3), ("校友", 3), ("学生", 2), ("student", 2)],
    "department": [("招聘", 4), ("news", 2), ("新闻", 2)],
    "admissions": [("alumni", 3), ("校友", 3), ("news", 2), ("新闻", 2)],
    "scholarship": [("news", 2), ("新闻", 2)],
}


def score_link(kind: str, text: str, url: str) -> float:
    t, u = (text or "").lower(), (url or "").lower()
    s = float(sum(w for kw, w in KEYWORDS[kind] if kw in t))
    s += 2.0 * min(sum(1 for h in URL_HINTS.get(kind, ()) if h in u), 2)
    s -= float(sum(w for kw, w in NEGATIVE.get(kind, ()) if kw in t or kw in u))
    return s
