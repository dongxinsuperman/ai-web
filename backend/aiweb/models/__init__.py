"""模型聚合导入，确保 create_all 能注册全部表。"""
from aiweb.models.asset import Asset
from aiweb.models.base import Base
from aiweb.models.config import ConfigKV
from aiweb.models.item import Item
from aiweb.models.run import Run, RunStep
from aiweb.models.site import Site
from aiweb.models.submission import Submission

__all__ = ["Base", "Asset", "ConfigKV", "Item", "Run", "RunStep", "Site", "Submission"]
