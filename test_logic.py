import queue
import re
import sys
import types
import unittest
from unittest import mock


def _install_import_stubs():
    """Install minimal module stubs so plugin modules can be imported."""
    mcdreforged = types.ModuleType("mcdreforged")
    api = types.ModuleType("mcdreforged.api")
    api_all = types.ModuleType("mcdreforged.api.all")

    class Serializable:
        pass

    class RText:
        def __init__(self, *args, **kwargs):
            pass

        def c(self, *args, **kwargs):
            return self

    class RTextList(RText):
        pass

    class RAction:
        run_command = "run_command"

    class SimpleCommandBuilder:
        def arg(self, *args, **kwargs):
            return self

        def command(self, *args, **kwargs):
            return self

        def register(self, *args, **kwargs):
            pass

    def new_thread(name=None):
        def decorator(func):
            return func

        return decorator

    class _Placeholder:
        pass

    for name, value in {
        "Serializable": Serializable,
        "RText": RText,
        "RTextList": RTextList,
        "RAction": RAction,
        "SimpleCommandBuilder": SimpleCommandBuilder,
        "new_thread": new_thread,
        "PluginServerInterface": _Placeholder,
        "ServerInterface": _Placeholder,
        "Info": _Placeholder,
        "CommandSource": _Placeholder,
        "CommandContext": _Placeholder,
        "Text": _Placeholder,
        "Number": _Placeholder,
    }.items():
        setattr(api_all, name, value)

    api.all = api_all
    mcdreforged.api = api
    sys.modules["mcdreforged"] = mcdreforged
    sys.modules["mcdreforged.api"] = api
    sys.modules["mcdreforged.api.all"] = api_all

    minecraft_data_api = types.ModuleType("minecraft_data_api")
    minecraft_data_api.get_player_coordinate = lambda player: None
    json_parser = types.ModuleType("minecraft_data_api.json_parser")

    class MinecraftJsonParser:
        def convert_minecraft_json(self, data):
            return {"raw": data}

    json_parser.MinecraftJsonParser = MinecraftJsonParser
    minecraft_data_api.json_parser = json_parser
    sys.modules["minecraft_data_api"] = minecraft_data_api
    sys.modules["minecraft_data_api.json_parser"] = json_parser


_install_import_stubs()

import main_storage_searcher.utils.range_finder as range_finder_module
from main_storage_searcher.utils.block_utils import BlockDataGetter, BlockTester
from main_storage_searcher.utils.hopper_matcher import HopperMatcher
from main_storage_searcher.utils.pos_utils import DynamicPos
from main_storage_searcher.utils.range_finder import RangeFinder


class FakeLogger:
    def __init__(self):
        self.warnings = []

    def warning(self, message, *args, **kwargs):
        self.warnings.append((message, args, kwargs))

    def info(self, message, *args, **kwargs):
        pass


class FakeServer:
    def __init__(self):
        self.logger = FakeLogger()
        self.executed = []

    def execute(self, command):
        self.executed.append(command)


class FakeInfo:
    def __init__(self, content):
        self.content = content
        self.is_from_server = True
        self.is_player = False


class ImmediateQueue(queue.Queue):
    """Queue whose get() never blocks, so tests do not wait for timeouts."""

    def get(self, block=True, timeout=None):
        return queue.Queue.get(self, block=False)


class EmptyQueue:
    def get(self, block=True, timeout=None):
        raise queue.Empty


class FakeRangeApi:
    def __init__(self, low, high, axis="z"):
        self.low = low
        self.high = high
        self.axis = axis

    def get_block_data(self, x, y, z, path=None):
        offset = z if self.axis == "z" else x
        block_id = (
            "minecraft:hopper"
            if self.low <= offset <= self.high
            else "minecraft:air"
        )
        return {"pos": (x, y, z), "data": {"id": block_id}}


class RangeFinderBoundaryTest(unittest.TestCase):
    def _find_boundary(self, low, high, search_step):
        finder = RangeFinder(
            FakeServer(),
            FakeRangeApi(low, high),
            HopperMatcher(),
        )
        with mock.patch.object(range_finder_module, "highlight_block_timer"):
            return finder._find_boundary(
                DynamicPos((0, 64, 0)),
                "z",
                search_step,
            )

    def test_positive_boundary_uses_expansion_and_bisection(self):
        self.assertEqual(self._find_boundary(0, 300, 256), 300)

    def test_negative_boundary_uses_expansion_and_bisection(self):
        self.assertEqual(self._find_boundary(-300, 0, -256), -300)


class BlockDataGetterTest(unittest.TestCase):
    def test_multi_block_data_keeps_positions_aligned(self):
        positions = [(0, 64, z) for z in range(6)]
        responded = {0, 2, 4}
        server = FakeServer()
        getter = BlockDataGetter(server)
        getter.queue = ImmediateQueue()

        def execute(command):
            server.executed.append(command)
            match = re.fullmatch(
                r"data get block (-?\d+) (-?\d+) (-?\d+)(?: .*)?",
                command,
            )
            self.assertIsNotNone(match)
            x, y, z = (int(value) for value in match.groups())
            index = z
            if index in responded:
                getter.on_info(
                    server,
                    FakeInfo(
                        f"{x}, {y}, {z} has the following block data: {{}}"
                    ),
                )
            else:
                getter.on_info(
                    server,
                    FakeInfo("The target block is not a block entity"),
                )

        server.execute = execute
        result = getter.get_multi_block_data(positions, path="id")

        self.assertEqual(len(result), len(positions))
        self.assertTrue(all(command.endswith(" id") for command in server.executed))
        for index, position in enumerate(positions):
            if index in responded:
                self.assertEqual(tuple(result[index]["pos"]), position)
            else:
                self.assertIsNone(result[index])
        self.assertEqual(getter.task_count, 0)

    def test_task_count_does_not_go_negative_after_mixed_paths(self):
        server = FakeServer()
        getter = BlockDataGetter(server)

        class InfoThenEmptyQueue:
            def __init__(self):
                self.called = False

            def put(self, item):
                pass

            def get(self, block=True, timeout=None):
                if not self.called:
                    self.called = True
                    getter.on_info(
                        server,
                        FakeInfo(
                            "0, 64, 0 has the following block data: {}"
                        ),
                    )
                raise queue.Empty

        getter.queue = InfoThenEmptyQueue()
        self.assertIsNone(getter.get_block_data(0, 64, 0))
        self.assertEqual(getter.task_count, 0)

        getter.on_info(
            server,
            FakeInfo("0, 64, 0 has the following block data: {}"),
        )
        self.assertEqual(getter.task_count, 0)

        tester = BlockTester(server)
        tester.queue = EmptyQueue()
        self.assertFalse(tester.test_block(0, 64, 0, "minecraft:hopper"))
        tester.on_info(server, FakeInfo("Test passed"))
        self.assertEqual(tester.task_count, 0)


class HopperMatcherTest(unittest.TestCase):
    """严格判断用于扫描（排除空的运输漏斗），宽松判断用于 range 探测。"""

    def setUp(self):
        self.matcher = HopperMatcher()

    def test_strict_rejects_hopper_without_full_contents(self):
        empty_hopper = {"id": "minecraft:hopper", "items": []}
        self.assertTrue(self.matcher.is_hopper_block(empty_hopper))
        self.assertFalse(self.matcher.is_valid_hopper(empty_hopper))

    def test_strict_accepts_storage_hopper(self):
        storage_hopper = {
            "id": "minecraft:hopper",
            "items": [{"slot": index, "id": "minecraft:diamond", "count": 1} for index in range(5)],
        }
        self.assertTrue(self.matcher.is_hopper_block(storage_hopper))
        self.assertTrue(self.matcher.is_valid_hopper(storage_hopper))

    def test_loose_rejects_non_hopper(self):
        self.assertFalse(self.matcher.is_hopper_block({"id": "minecraft:chest"}))
        self.assertFalse(self.matcher.is_hopper_block(None))


class FindAllChestsTest(unittest.TestCase):
    """部分漏斗找不到箱子时保留空组占位，全部找不到才抛错。"""

    def _creator(self):
        from main_storage_searcher.utils.main_storage_utils import MainStorageCreator

        return MainStorageCreator(FakeServer())

    def _fake_finder(self, mapping, raises=()):
        class FakeChestFinder:
            def find_target_chest(self, pos, axis):
                key = tuple(int(value) for value in pos)
                if key in raises:
                    raise RuntimeError("boom")
                return mapping.get(key)

            def complete_chest_group(self, pos, axis):
                return [tuple(int(value) for value in pos)]

        return FakeChestFinder()

    def test_partial_failure_keeps_alignment(self):
        creator = self._creator()
        hoppers = [(index, 64, 0) for index in range(4)]
        mapping = {hoppers[0]: hoppers[0], hoppers[3]: hoppers[3]}
        chests = creator._find_all_chests(
            hoppers, "x", self._fake_finder(mapping, raises={hoppers[1]})
        )
        self.assertEqual(len(chests), len(hoppers))
        self.assertEqual(chests[0], [hoppers[0]])
        self.assertEqual(chests[1], [])
        self.assertEqual(chests[2], [])
        self.assertEqual(chests[3], [hoppers[3]])

    def test_total_failure_raises(self):
        from main_storage_searcher.utils.storage_scanner import MainStorageCreatingError

        creator = self._creator()
        hoppers = [(index, 64, 0) for index in range(3)]
        with self.assertRaises(MainStorageCreatingError):
            creator._find_all_chests(hoppers, "x", self._fake_finder({}))


if __name__ == "__main__":
    unittest.main()
