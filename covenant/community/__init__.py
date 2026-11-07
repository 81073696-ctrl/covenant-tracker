"""社区战绩收集：一个可自托管的记录汇总服务 + 客户端上传工具。

定位
----
卫戍协议没有官方战绩 API，所以「全服 pick 率」只能靠玩家自愿贡献。
这里的两个部件刻意做得很小：

* :mod:`covenant.community.server`：收 JSONL、按 id 去重、聚合成统计；
* :mod:`covenant.community.client`：把本机 ``var/matches.jsonl`` 推上去。

记录里没有账号信息，只有对局本身的公开信息（模式、回合、阵容、结果）。
"""

from .client import submit_file, submit_records
from .server import CommunityServer, RecordBank

__all__ = ["CommunityServer", "RecordBank", "submit_file", "submit_records"]
