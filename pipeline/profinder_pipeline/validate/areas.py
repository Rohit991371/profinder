"""Research-area classifier (task 4.5). Output is ALWAYS a subset of the app's 24 area names, decided ONLY from the
professor's own interests/lab text (never from the department or university name), each with the evidence terms found.

Rule-based and deterministic (free, no Groq quota). An optional LLM second opinion (extract/llm.py) may only ADD an area if it
quotes a phrase that really occurs in the text. 'Computer Science' is assigned only when the text literally says so."""
import re
from dataclasses import dataclass

from ..schemas.enums import RESEARCH_AREAS

# area -> terms (case-insensitive). Any one term is evidence. Keep terms specific: broad words create the false tags we want to avoid.
RULES: dict[str, tuple[str, ...]] = {
    "Artificial Intelligence": ("人工智能", "artificial intelligence", "智能体", "知识图谱", "知识表示", "智能规划"),
    "Machine Learning": ("机器学习", "machine learning", "统计学习", "强化学习", "reinforcement learning", "联邦学习"),
    "Deep Learning": ("深度学习", "deep learning", "神经网络", "neural network"),
    "Computer Vision": ("计算机视觉", "computer vision", "图像识别", "目标检测", "视频理解", "三维重建", "图像分割"),
    "Natural Language Processing": ("自然语言处理", "natural language", "机器翻译", "语言模型", "语义理解", "文本生成"),
    "Generative AI": ("生成式", "generative ai", "生成模型", "扩散模型", "aigc", "大模型", "大语言模型", "large language model"),
    "Databases": ("数据库", "database", "查询优化", "数据管理", "dbms"),
    "Data Science": ("数据挖掘", "data mining", "大数据", "big data", "数据科学", "data science", "数据分析"),
    "Distributed Systems": ("分布式", "distributed system", "云计算", "cloud computing"),
    "Operating Systems": ("操作系统", "operating system", "内核", "kernel"),
    "Computer Networks": ("计算机网络", "computer network", "无线网络", "网络协议", "软件定义网络", "数据中心网络", "物联网", "wireless network"),
    "Network Security": ("网络安全", "network security", "入侵检测"),
    "Cybersecurity": ("信息安全", "网络空间安全", "cybersecurity", "cyber security", "系统安全", "软件安全", "漏洞", "恶意代码"),
    "Cryptography": ("密码学", "cryptograph", "公钥", "同态加密", "零知识", "密码算法", "密码协议"),
    "Privacy": ("隐私", "privacy", "差分隐私"),
    "AI Security": ("人工智能安全", "ai安全", "对抗样本", "adversarial", "模型安全", "大模型安全"),
    "Cloud Security": ("云安全", "cloud security"),
    "Digital Forensics": ("数字取证", "电子取证", "forensic"),
    "Human-Computer Interaction": ("人机交互", "human-computer interaction", "human computer interaction", "人机协作", "用户界面"),
    "Robotics": ("机器人", "robot"),
    "Software Engineering": ("软件工程", "software engineering", "程序分析", "软件测试", "软件缺陷", "软件开发", "程序设计语言"),
    "Algorithms": ("算法设计", "近似算法", "计算复杂性", "理论计算机", "图算法", "algorithm design", "theoretical computer science", "计算复杂度"),
    "Computer Science": ("计算机科学", "computer science"),
}
# Blockchain research counts as 'Blockchain Security' only when security is also stated (the app has no plain blockchain area).
_BLOCKCHAIN = ("区块链", "blockchain")
_SECURITY = ("安全", "security")

assert set(RULES) | {"Blockchain Security"} == set(RESEARCH_AREAS), "areas.py must cover exactly the app's 24 areas"


@dataclass
class AreaHit:
    area: str
    terms: list[str]


def classify_areas(*texts: str, max_areas: int = 5) -> list[AreaHit]:
    """Areas supported by explicit terms in the given interests/lab text. [] is a valid answer."""
    text = " ".join(t for t in texts if t).casefold()
    if not text.strip():
        return []
    hits: list[AreaHit] = []
    for area, terms in RULES.items():
        found = [t for t in terms if t.casefold() in text]
        if found:
            hits.append(AreaHit(area, found))
    if any(b in text for b in _BLOCKCHAIN) and any(s in text for s in _SECURITY):
        hits.append(AreaHit("Blockchain Security", [b for b in _BLOCKCHAIN if b in text]))
    # keep a stable, most-specific-first order: more evidence terms first, then name
    hits.sort(key=lambda h: (-len(h.terms), h.area))
    return hits[:max_areas]


def area_names(hits: list[AreaHit]) -> list[str]:
    return [h.area for h in hits]
