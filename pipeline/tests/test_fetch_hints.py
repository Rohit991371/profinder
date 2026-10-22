import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from profinder_pipeline.fetch.hints import Hints, load_allowed_domains, norm_name

CFG = Path(__file__).parent.parent / "config"


def test_shipped_hints_render_only_the_sjtu_directory_not_its_profiles():
    h = Hints.load(CFG / "fetch_hints.yml")
    d = h.lookup("https://www.cs.sjtu.edu.cn/jiaoshiml.html")
    assert d.render == "always" and d.wait_selector == ".rc-list .rc-item"
    assert h.lookup("https://www.cs.sjtu.edu.cn/jiaoshiml/zangbinyu.html").render == "auto"
    assert h.lookup("https://www.cs.tsinghua.edu.cn/szzk/jzgml.htm").render == "auto"
    assert Hints.load(CFG / "missing.yml").lookup("https://a.edu.cn/").render == "auto"


def test_domain_loader_accepts_the_possible_config_shapes(tmp_path):
    (tmp_path / "domains.yml").write_text(
        "Tsinghua University:\n  domains: [tsinghua.edu.cn]\n  email_domains: [cernet.edu.cn]\n"
        "Peking University: [pku.edu.cn, www.stu.pku.edu.cn]\n", encoding="utf-8")
    (tmp_path / "universities.yml").write_text(
        "universities:\n  - name: Fudan University\n    nameZh: 复旦大学\n    domains: [fudan.edu.cn]\n"
        "    extra_domains: ['https://www.cs.fudan.edu.cn/']\n    extra_email_domains: [fd.edu.cn]\n"
        "    seed_urls: {faculty_list: 'https://other.example.org/x'}\n", encoding="utf-8")
    d = load_allowed_domains(tmp_path)
    assert d[norm_name("Tsinghua University")] == ["tsinghua.edu.cn"]            # email domain excluded
    assert d[norm_name("Peking University")] == ["pku.edu.cn", "stu.pku.edu.cn"]
    assert d[norm_name("Fudan University")] == ["fudan.edu.cn", "cs.fudan.edu.cn"]  # seed url host not added, email excluded
    assert norm_name("East China University of Science and Technology") == norm_name("East China  University of Science and Technology")
