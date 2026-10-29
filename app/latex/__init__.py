"""LaTeX 讲义生成：题库数据 → ExBook 源 → 编译 → 发布物。

对外接口见 app/latex/build.py：build_release / rebuild_release。
"""
from __future__ import annotations

from app.latex import exbook  # noqa: F401
from app.latex.build import build_release, make_snapshot, rebuild_release  # noqa: F401

__all__ = ["build_release", "rebuild_release", "make_snapshot", "exbook"]
