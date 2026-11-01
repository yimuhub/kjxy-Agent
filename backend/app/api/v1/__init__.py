"""API v1 包。

总路由在 ``router.py`` 聚合（``app.main`` 直接导入），其中包含
``/commander/*``（指挥官编排：执行/重试/取消）。这里保留同名导出，
方便按 ``from app.api.v1 import router`` 的写法使用同一套路由。
"""

from app.api.v1.router import router

__all__ = ["router"]
