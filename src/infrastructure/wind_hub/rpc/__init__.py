"""wind-hub Commander protobuf 契约快照。

检入的 pb2 由 protobuf 6.31 gencode 生成，在 6.33 运行时下不再
隐式预载 well-known types 与跨文件依赖；导入子模块前必须先把
google.protobuf 标准文件和 commander.proto 载入默认描述符池。
后续重新生成 pb2（6.33 gencode）后可移除该兼容序列。
"""
from google.protobuf import empty_pb2 as _empty_pb2  # noqa: F401
from google.protobuf import timestamp_pb2 as _timestamp_pb2  # noqa: F401
from google.protobuf import wrappers_pb2 as _wrappers_pb2  # noqa: F401

from . import commander_pb2 as _commander_pb2  # noqa: F401
