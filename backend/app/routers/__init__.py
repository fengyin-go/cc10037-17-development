"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import business, evidence, resource

# evidence 含固定前缀 /api/evidence/*，必须在通用资源路由 /api/{module} 之前注册，
# 否则 /api/evidence/executions 会被动态路径当成 module=evidence 抢先匹配。
ROUTERS = [evidence, resource, business]
