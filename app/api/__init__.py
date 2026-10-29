"""接口注册入口。

每个业务模块在导入时通过 app.server 的装饰器登记路由。
"""
from __future__ import annotations


def register_all() -> None:
    from app.api import workspace, content, teaching, system, ai, corrections, layout  # noqa: F401
