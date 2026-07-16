"""ASoC Domain Knowledge Package (DKP).

This is the ONLY area of the tree where ALSA / ASoC / snd_soc / sound/soc
identifiers may appear (Domain Isolation, Constitution Sec. 9). The Generic
Runtime never imports this package by name; it is discovered via the
``kri.dkp`` entry-point group declared in ``pyproject.toml``.
"""
