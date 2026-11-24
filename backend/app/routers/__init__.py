"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import recovery, resource

# recovery 含固定前缀 /api/recovery，需在动态 /api/{module} 资源路由之前注册，
# 否则固定路径会被动态路径抢先匹配。
ROUTERS = [recovery, resource]
