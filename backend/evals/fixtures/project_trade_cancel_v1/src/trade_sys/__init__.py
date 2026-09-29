"""Fruit Sales System - 水果销售系统。

依据 UML 设计工程 design/trade_sys_0822.umlproj 生成的可执行 Python 实现。
包内按设计组件（component）与领域（domain）分层组织：

- models   : 各领域类图中的实体类
- services : 各组件提供的服务
- database : 对应组件图中的 Database 组件（内存持久化）
- web      : 对应 WebFrontend 组件的视图层
- app      : 组装各组件并暴露 WebFrontend 门面
"""

__version__ = "1.0.0"
