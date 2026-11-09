import queue
import re
import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple, TypedDict

from mcdreforged.api.all import Info, ServerInterface
from minecraft_data_api.json_parser import MinecraftJsonParser


# Since Minecraft 26.1, command feedback printed to the server console is
# prefixed with "System chat: ". Strip it before pattern matching so the same
# parsers keep working across old and new server versions.
FEEDBACK_PREFIXES = (
    "System chat: ",
)


def strip_feedback_prefix(content: str) -> str:
    """Remove the console feedback prefix newer server versions add."""
    for prefix in FEEDBACK_PREFIXES:
        if content.startswith(prefix):
            return content[len(prefix):]
    return content


def normalize_nbt_key(key: str) -> str:
    """Normalize NBT keys to lowercase names without underscores."""
    return key.lower().replace("_", "")


def normalize_nbt_data(value: Any) -> Any:
    """Recursively normalize dictionary keys in parsed Minecraft data."""
    if isinstance(value, dict):
        return {
            normalize_nbt_key(key): normalize_nbt_data(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [normalize_nbt_data(item) for item in value]
    return value


class AbstractDataGetter(ABC):

    def __init__(self, server: ServerInterface) -> None:
        self.server = server
        self.queue = queue.Queue()
        self.task_count = 0
        self._lock = threading.Lock()
        self.pattern: re.Pattern
    
    @abstractmethod
    def on_info(self, server: ServerInterface, info: Info):
        ...

class BlockData(TypedDict):
    pos: Tuple[int, int, int]
    data: dict

class BlockDataGetter(AbstractDataGetter):

    def __init__(self, server: ServerInterface) -> None:
        super().__init__(server)
        self.pattern = re.compile(r"^(?P<x>-?[0-9]+), (?P<y>-?[0-9]+), (?P<z>-?[0-9]+) has the following block data: (?P<data>.+)$")

    def get_block_data(self, x, y, z, path = None) -> Optional[BlockData]:
        path = " "+path if path else ""
        requested_pos = (int(x), int(y), int(z))
        with self._lock:
            self.task_count += 1
        self.server.execute(f"data get block {int(x)} {int(y)} {int(z)}"+path)
        deadline = time.monotonic() + 5
        try:
            while (remaining := deadline - time.monotonic()) > 0:
                item = self.queue.get(timeout=remaining)
                # 迟到的旧响应可能留在共享队列中，不能当作本次查询的数据。
                if item is None or tuple(int(c) for c in item["pos"]) == requested_pos:
                    return item
        except queue.Empty:
            pass
        with self._lock:
            self.task_count = max(0, self.task_count - 1)
        return None

    def get_multi_block_data(self, multi_pos: List[Tuple[int, int, int]], path = None) -> List[Optional[BlockData]]:
        results: List[Optional[BlockData]] = [None] * len(multi_pos)
        index_of = {tuple(int(c) for c in pos): i for i, pos in enumerate(multi_pos)}
        path = " "+path if path else ""
        for pos in multi_pos:
            x, y, z = pos
            with self._lock:
                self.task_count += 1
            self.server.execute(f"data get block {int(x)} {int(y)} {int(z)}"+path)

        # 所有命令共用同一个截止时间，避免按位置串行等待 5 秒。
        deadline = time.monotonic() + max(5.0, len(multi_pos) * 0.5)
        received = 0
        for _ in range(len(multi_pos)):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                item = self.queue.get(timeout=remaining)
                received += 1
                # 按坐标归位，缺失或无坐标的错误回显不能让后续数据前移。
                if isinstance(item, dict):
                    index = index_of.get(tuple(int(c) for c in item["pos"]))
                    if index is not None:
                        results[index] = item
            except queue.Empty:
                break

        if received < len(multi_pos):
            missing = len(multi_pos) - received
            with self._lock:
                self.task_count = max(0, self.task_count - missing)
            self.server.logger.warning(
                "收到 %s 条响应 / 期望 %s 条；等待方块数据超时。",
                received,
                len(multi_pos),
            )
        return results

    def on_info(self, server: ServerInterface, info: Info):
        if not info.is_from_server:
            return
        with self._lock:
            if self.task_count <= 0:
                return
        content = strip_feedback_prefix(info.content)
        if (result := self.pattern.match(content)) is not None:
            parsed_data = MinecraftJsonParser().convert_minecraft_json(result.group("data"))
            self.queue.put({
                "pos": (
                    float(result.group("x")),
                    float(result.group("y")),
                    float(result.group("z")),
                ),
                "data": normalize_nbt_data(parsed_data),
            })
            with self._lock:
                self.task_count = max(0, self.task_count - 1)
        elif content in ["That position is not loaded", "The target block is not a block entity"]:
            self.queue.put(None)
            with self._lock:
                self.task_count = max(0, self.task_count - 1)

class BlockTester(AbstractDataGetter):
    """串行测试方块：回显不带坐标，多个在途请求无法可靠关联结果。"""
    
    def __init__(self, server: ServerInterface) -> None:
        super().__init__(server)
        self._request_lock = threading.Lock()
        self.pattern = re.compile(r"^Test (?P<status>(?:passed)|(?:failed))$")
        self.invalid_property_pattern = re.compile(r"^Block .+ does not accept .+ for .+ property")

    def test_block(self, x, y, z, block):
        self._request_lock.acquire()
        try:
            with self._lock:
                self.task_count += 1
            self.server.execute(f"execute if block {int(x)} {int(y)} {int(z)} {block}")
            try:
                return self.queue.get(timeout=5)
            except queue.Empty:
                with self._lock:
                    self.task_count = max(0, self.task_count - 1)
                return False
        finally:
            self._request_lock.release()

    def on_info(self, server: ServerInterface, info: Info):
        if not info.is_from_server:
            return
        with self._lock:
            if self.task_count <= 0:
                return
        content = strip_feedback_prefix(info.content)
        if (result := self.pattern.match(content)) is not None:
            self.queue.put(result.group("status") == "passed")
            with self._lock:
                self.task_count = max(0, self.task_count - 1)
            # self.server.logger.info(result.group("status"))
        elif self.invalid_property_pattern.match(content):
            # e.g. "Block minecraft:hopper does not accept 'up' for facing property"
            # The server rejects the predicate instead of replying "Test failed",
            # so answer False here to keep the caller from waiting out its timeout.
            self.queue.put(False)
            with self._lock:
                self.task_count = max(0, self.task_count - 1)
