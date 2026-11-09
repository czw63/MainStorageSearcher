import queue
import re
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
        self.task_count += 1
        self.server.execute(f"data get block {int(x)} {int(y)} {int(z)}"+path)
        try:
            return self.queue.get(timeout=5)
        except queue.Empty:
            self.task_count = max(0, self.task_count - 1)
            return None

    def get_multi_block_data(self, multi_pos: List[Tuple[int, int, int]], path = None) -> List[BlockData]:
        multi_block_data = []
        for pos in multi_pos:
            x, y, z = pos
            self.task_count += 1
            self.server.execute(f"data get block {int(x)} {int(y)} {int(z)}")
        for i in range(len(multi_pos)):
            try:
                if (result := self.queue.get(timeout=5)):
                    multi_block_data.append(result)
            except queue.Empty:
                self.task_count = max(0, self.task_count - 1)
        return multi_block_data

    def on_info(self, server: ServerInterface, info: Info):
        if not info.is_from_server or self.task_count == 0:
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
            self.task_count -= 1
        elif content in ["That position is not loaded", "The target block is not a block entity"]:
            self.queue.put(None)
            self.task_count -= 1

class BlockTester(AbstractDataGetter):
    
    def __init__(self, server: ServerInterface) -> None:
        super().__init__(server)
        self.pattern = re.compile(r"^Test (?P<status>(?:passed)|(?:failed))$")
        self.invalid_property_pattern = re.compile(r"^Block .+ does not accept .+ for .+ property")

    def test_block(self, x, y, z, block):
        self.task_count += 1
        self.server.execute(f"execute if block {int(x)} {int(y)} {int(z)} {block}")
        # self.server.logger.info(f"execute if block {int(x)} {int(y)} {int(z)} {block}")
        try:
            return self.queue.get(timeout=5)
        except queue.Empty:
            self.task_count = max(0, self.task_count - 1)
            return False

    def on_info(self, server: ServerInterface, info: Info):
        if not info.is_from_server or self.task_count == 0:
            return
        content = strip_feedback_prefix(info.content)
        if (result := self.pattern.match(content)) is not None:
            self.queue.put(result.group("status") == "passed")
            self.task_count -= 1
            # self.server.logger.info(result.group("status"))
        elif self.invalid_property_pattern.match(content):
            # e.g. "Block minecraft:hopper does not accept 'up' for facing property"
            # The server rejects the predicate instead of replying "Test failed",
            # so answer False here to keep the caller from waiting out its timeout.
            self.queue.put(False)
            self.task_count -= 1

