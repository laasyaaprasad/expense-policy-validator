"""Experiments run after the config freeze. None changes the submitted system."""

from validator.data import ROOT

# What experiments 1 to 5 were run on: the classifier's outputs at temperature 1, before the switch to 0
# (DECISIONS.md, section 4). They read these, not the current outputs, so a rerun replays what they recorded.
TEMPERATURE_1 = ROOT / "outputs" / "temperature-1"
