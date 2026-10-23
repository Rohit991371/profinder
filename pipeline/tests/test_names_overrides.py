"""Needs pypinyin. Polyphonic surnames must use the surname reading (翟 is Zhai, not Di)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from profinder_pipeline.utils.names import SURNAME_OVERRIDES, romanise


def test_zhai_and_other_overrides():
    assert SURNAME_OVERRIDES["翟"] == "Zhai" and SURNAME_OVERRIDES["祭"] == "Zhai" and SURNAME_OVERRIDES["阚"] == "Kan"
    assert romanise("翟季冬")[0] == "Zhai Jidong" and romanise("翟季冬")[2] == "Zhai"
    assert romanise("单学志")[2] == "Shan"          # from Iteration 1, unchanged
