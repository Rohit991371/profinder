import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import re

import yaml

from profinder_pipeline.extract.sitecfg import load_site_configs
from extract_helpers import CONFIG, ECNU, SJTU, TSINGHUA


def test_all_site_configs_are_valid():
    cfgs = load_site_configs()
    assert {TSINGHUA, SJTU, ECNU} <= set(cfgs)
    seeds = {u["name"] for u in yaml.safe_load((CONFIG / "universities.yml").read_text(encoding="utf-8"))["universities"]}
    domains = yaml.safe_load((CONFIG / "domains.yml").read_text(encoding="utf-8"))["universities"]
    for name, c in cfgs.items():
        assert name in seeds and name in domains and domains[name]["domains"]
        assert c.list_url.startswith(("http://", "https://")) and c.min_items >= 1
        assert c.school_en or c.profile_spec.get("department_is_school")        # a school name from the site, or from each profile page
        assert "name" in c.list_spec["fields"] and "profile_href" in c.list_spec["fields"]
        for p in c.personal_url_patterns:
            re.compile(p)


def test_suppress_file_shape():
    d = yaml.safe_load((CONFIG / "suppress.yml").read_text(encoding="utf-8"))
    assert isinstance(d["emails"], list) and isinstance(d["names"], list)

