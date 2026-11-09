import queue
import re
import sys
import threading
import types
import unittest
from functools import wraps
from unittest import mock


def start_worker(func, *args, **kwargs):
    def run():
        try:
            worker.result = func(*args, **kwargs)
        except BaseException as error:
            worker.error = error

    worker = threading.Thread(target=run, daemon=True)
    worker.result = None
    worker.error = None
    worker.start()
    return worker


def finish_worker(worker, timeout=8):
    worker.join(timeout)
    if worker.is_alive():
        raise AssertionError("Worker did not finish")
    if worker.error is not None:
        raise worker.error
    return worker.result


def _install_import_stubs():
    """Install minimal module stubs so plugin modules can be imported."""
    mcdreforged = types.ModuleType("mcdreforged")
    api = types.ModuleType("mcdreforged.api")
    api_all = types.ModuleType("mcdreforged.api.all")

    class Serializable:
        pass

    class RText:
        def __init__(self, *args, **kwargs):
            self.text = "".join(str(arg) for arg in args)

        def __str__(self):
            return self.text

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
            @wraps(func)
            def threaded(*args, **kwargs):
                return start_worker(func, *args, **kwargs)
            return threaded

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
        "Integer": _Placeholder,
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
import main_storage_searcher.main_storage as main_storage_module
import main_storage_searcher.utils.chest_finder as chest_finder_module
import main_storage_searcher.utils.storage_scanner as scanner_module
from main_storage_searcher.main_storage import MainStorageManager
from main_storage_searcher.utils.block_utils import BlockDataGetter, BlockTester
from main_storage_searcher.utils.chest_finder import ChestFinder
from main_storage_searcher.utils.config_utils import Config
from main_storage_searcher.utils.hopper_matcher import HopperMatcher
from main_storage_searcher.utils.pos_utils import DynamicPos
from main_storage_searcher.utils.range_finder import RangeFinder


class FakeLogger:
    def __init__(self):
        self.warnings = []
        self.infos = []

    def warning(self, message, *args, **kwargs):
        self.warnings.append((message, args, kwargs))

    def info(self, message, *args, **kwargs):
        self.infos.append((message, args, kwargs))


class FakeServer:
    def __init__(self):
        self.logger = FakeLogger()
        self.executed = []

    def execute(self, command):
        self.executed.append(command)

    def register_help_message(self, *args):
        pass

    def broadcast(self, *args):
        pass


class FakeInfo:
    def __init__(self, content):
        self.content = content
        self.is_from_server = True
        self.is_player = False


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
    def _multi_response(self, failure=False, reverse=False):
        positions = [(0, 64, z) for z in range(3)]
        server = FakeServer()
        getter = BlockDataGetter(server)
        commands_sent = threading.Event()

        def execute(command):
            server.executed.append(command)
            if len(server.executed) == 3:
                commands_sent.set()

        server.execute = execute
        worker = start_worker(getter.get_multi_block_data, positions, path="id")
        self.assertTrue(commands_sent.wait(2))
        for index in ([2, 1, 0] if reverse else [0, 1, 2]):
            if index == 1:
                if failure:
                    getter.on_info(server, FakeInfo("The target block is not a block entity"))
                # 真缺失时完全不调用 on_info，后续成功响应仍必须归位。
                continue
            x, y, z = positions[index]
            getter.on_info(server, FakeInfo(f"{x}, {y}, {z} has the following block data: {{}}"))
        result = finish_worker(worker)

        self.assertEqual(len(result), len(positions))
        self.assertTrue(all(command.endswith(" id") for command in server.executed))
        self.assertEqual(result, [
            {"pos": positions[0], "data": {"raw": "{}"}},
            None,
            {"pos": positions[2], "data": {"raw": "{}"}},
        ])
        self.assertEqual(getter.task_count, 0)
        if not failure:
            self.assertEqual(server.logger.warnings[0][1], (2, 3))
        else:
            self.assertEqual(server.logger.warnings, [])

    def test_multi_block_data_missing_middle_response(self):
        self._multi_response()

    def test_multi_block_data_failure_response(self):
        self._multi_response(failure=True)

    def test_multi_block_data_out_of_order(self):
        self._multi_response(failure=True, reverse=True)

    def test_single_query_discards_late_response(self):
        server = FakeServer()
        getter = BlockDataGetter(server)
        self.assertIsNone(getter.get_block_data(0, 64, 0))
        self.assertEqual(getter.task_count, 0)
        getter.queue.put({"pos": (0, 64, 0), "data": {"raw": "old"}})
        sent = threading.Event()
        server.execute = lambda command: sent.set()
        worker = start_worker(getter.get_block_data, 1, 64, 0)
        self.assertTrue(sent.wait(2))
        getter.on_info(server, FakeInfo("1, 64, 0 has the following block data: {}"))
        self.assertEqual(finish_worker(worker)["pos"], (1, 64, 0))
        self.assertEqual(getter.task_count, 0)

    def test_single_failure_response_returns_none(self):
        server = FakeServer()
        getter = BlockDataGetter(server)
        server.execute = lambda command: getter.on_info(
            server, FakeInfo("The target block is not a block entity"))
        self.assertIsNone(getter.get_block_data(0, 64, 0))
        self.assertEqual(getter.task_count, 0)

    def test_task_count_with_concurrent_waiters_and_late_info(self):
        server = FakeServer()
        getter = BlockDataGetter(server)
        sent = queue.Queue()
        server.execute = sent.put
        workers = [start_worker(getter.get_block_data, index, 64, 0) for index in range(2)]
        for _ in workers:
            self.assertIsNotNone(sent.get(timeout=2))
        self.assertEqual(getter.task_count, 2)
        # 两条失败回显都真实进入队列，唤醒两个等待线程。
        for _ in workers:
            getter.on_info(server, FakeInfo("The target block is not a block entity"))
        self.assertEqual([finish_worker(worker) for worker in workers], [None, None])
        getter.on_info(server, FakeInfo("0, 64, 0 has the following block data: {}"))
        self.assertEqual(getter.task_count, 0)
        self.assertTrue(getter.queue.empty())


class BlockTesterTest(unittest.TestCase):
    def test_requests_are_serialized(self):
        server = FakeServer()
        tester = BlockTester(server)
        sent = queue.Queue()
        server.execute = sent.put
        first = start_worker(tester.test_block, 0, 64, 0, "hopper")
        self.assertIn("0 64 0", sent.get(timeout=2))
        second_started = threading.Event()

        def second_query():
            second_started.set()
            return tester.test_block(1, 64, 0, "hopper")

        second = start_worker(second_query)
        self.assertTrue(second_started.wait(2))
        with self.assertRaises(queue.Empty):
            sent.get(timeout=0.1)
        self.assertEqual(tester.task_count, 1)
        tester.on_info(server, FakeInfo("Test passed"))
        self.assertTrue(finish_worker(first))
        self.assertIn("1 64 0", sent.get(timeout=2))
        tester.on_info(server, FakeInfo("Test failed"))
        self.assertFalse(finish_worker(second))
        self.assertEqual(tester.task_count, 0)

    def test_timeout_releases_request_lock_and_ignores_idle_info(self):
        server = FakeServer()
        tester = BlockTester(server)
        self.assertFalse(tester.test_block(0, 64, 0, "minecraft:hopper"))
        tester.on_info(server, FakeInfo("Test passed"))
        self.assertEqual(tester.task_count, 0)
        self.assertTrue(tester.queue.empty())
        server.execute = lambda command: tester.on_info(server, FakeInfo("Test passed"))
        self.assertTrue(tester.test_block(1, 64, 0, "hopper"))
        self.assertEqual(tester.task_count, 0)

    def test_execute_exception_releases_request_lock(self):
        server = FakeServer()
        tester = BlockTester(server)
        with mock.patch.object(server, "execute", side_effect=RuntimeError("execute failed")):
            with self.assertRaises(RuntimeError):
                tester.test_block(0, 64, 0, "hopper")
        self.assertTrue(tester._request_lock.acquire(blocking=False))
        tester._request_lock.release()


class ChestFinderPathTest(unittest.TestCase):
    def setUp(self):
        # A -> B -> C -> D 首次被入口方向挡住；A 下方再次进入 D 后可走 E。
        self.blocks = {
            (1, 3, 0): ("hopper", "west"),
            (0, 3, 0): ("hopper", "down"),
            (0, 2, 0): ("hopper", "east"),
            (1, 2, 0): ("hopper", "west"),
            (1, 1, 0): ("hopper", "down"),
            (1, 0, 0): ("chest", None),
        }
        blocks = self.blocks

        class GraphApi:
            def get_block_data(self, x, y, z, path=None):
                pos = (x, y, z)
                if pos not in blocks:
                    return None
                return {"pos": pos, "data": "minecraft:" + blocks[pos][0]}

        class GraphTester:
            def test_block(self, x, y, z, block):
                kind, facing = blocks.get((x, y, z), ("air", None))
                return block == kind or block == f"{kind}[facing={facing}]"

        self.finder = ChestFinder(FakeServer(), GraphApi(), GraphTester())
        self.visuals = mock.patch.multiple(
            chest_finder_module, highlight_block=mock.DEFAULT, highlight_block_timer=mock.DEFAULT)
        self.visuals.start()
        self.addCleanup(self.visuals.stop)

    def test_revisits_position_from_another_facing_after_backtracking(self):
        visited = set()
        self.assertEqual(
            self.finder.find_target_chest(DynamicPos((1, 3, 0)), "x", visited=visited),
            (1, 0, 0),
        )
        self.assertEqual(visited, set())

    def test_entry_facing_is_part_of_path_state(self):
        previous_state = ((1, 2, 0), "hopper", "west")
        visited = {previous_state}
        self.assertEqual(
            self.finder.find_target_chest(
                DynamicPos((1, 2, 0)), "x", source_facing="up", visited=visited),
            (1, 0, 0),
        )
        self.assertEqual(visited, {previous_state})

    def test_same_state_cycle_is_rejected(self):
        self.blocks.clear()
        self.blocks.update({
            (0, 0, 0): ("dropper", "east"),
            (1, 0, 0): ("dropper", "down"),
            (1, -1, 0): ("dropper", "west"),
            (0, -1, 0): ("dropper", "up"),
        })
        visited = set()
        self.assertIsNone(self.finder.find_target_chest(
            DynamicPos((0, 0, 0)), "x", hopper=False, visited=visited))
        self.assertEqual(visited, set())
        self.assertTrue(any("cycle" in warning[0] for warning in self.finder.server.logger.warnings))

    def test_depth_limit_is_preserved(self):
        self.assertIsNone(self.finder.find_target_chest(DynamicPos((1, 3, 0)), "x", depth=64))
        self.assertIn("depth", self.finder.server.logger.warnings[0][0])

    def test_exception_cleans_current_path(self):
        visited = set()
        with mock.patch.object(self.finder.block_tester, "test_block", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.finder.find_target_chest(DynamicPos((1, 3, 0)), "x", visited=visited)
        self.assertEqual(visited, set())


class StorageScannerPlaneTest(unittest.TestCase):
    def _scan(self, hoppers):
        class ScanApi:
            def get_multi_block_data(self, positions):
                return [
                    {"pos": pos, "data": {
                        "id": "minecraft:hopper",
                        "items": [{"slot": i, "id": "minecraft:diamond", "count": 1} for i in range(5)],
                    }} if pos in hoppers else None
                    for pos in positions
                ]

        server = FakeServer()
        scanner = scanner_module.MainStorageScanner(server, ScanApi(), HopperMatcher())
        with mock.patch.object(scanner_module, "highlight_block_multi"), mock.patch.object(scanner_module, "rtr"):
            result = scanner.scan_hoppers((10, 64, 20), time_step=0)
        self.assertTrue(server.logger.infos)
        self.assertEqual(server.logger.infos[0][1], (1,))
        return result

    def test_z_axis_filters_earlier_x_hopper(self):
        self.assertEqual(self._scan({
            (11, 79, 20), (10, 79, 21), (10, 79, 22),
        }), ([(10, 79, 21), (10, 79, 22)], "z"))

    def test_x_axis_filters_earlier_z_hopper(self):
        self.assertEqual(self._scan({
            (10, 79, 21), (11, 78, 20), (12, 78, 20),
        }), ([(11, 78, 20), (12, 78, 20)], "x"))

    def test_empty_player_plane_is_rejected(self):
        scanner = scanner_module.MainStorageScanner(FakeServer(), None, HopperMatcher())
        with mock.patch.object(scanner_module, "highlight_block_multi"), mock.patch.object(scanner_module, "rtr"):
            with mock.patch.object(scanner, "_find_new_hoppers", side_effect=[
                [(1, 79, 1), (2, 79, 1)],
            ] + [[]] * 20):
                with self.assertRaisesRegex(scanner_module.MainStorageCreatingError, "玩家所在平面"):
                    scanner.scan_hoppers((10, 64, 20), time_step=0)


class FakeSource:
    is_player = True
    player = "tester"

    def __init__(self):
        self.replies = []

    def has_permission(self, permission):
        return True

    def reply(self, message):
        self.replies.append(str(message))


class MainStorageManagerTest(unittest.TestCase):
    def setUp(self):
        self.translation = mock.patch.object(main_storage_module, "rtr", side_effect=lambda key, *a, **kw: key)
        self.translation.start()
        self.addCleanup(self.translation.stop)
        self.visuals = mock.patch.multiple(
            main_storage_module,
            highlight_block_by_entity=mock.DEFAULT,
            highlight_block_multi=mock.DEFAULT,
        )
        self.effects = self.visuals.start()
        self.addCleanup(self.visuals.stop)
        # 仅去掉视觉循环的等待，处理函数和命令装饰器仍按真实逻辑执行。
        self.clock = mock.patch.object(main_storage_module, "time", types.SimpleNamespace(sleep=lambda seconds: None))
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.manager = MainStorageManager(FakeServer(), Config())
        self.data = {
            "name": "test", "axis": "x", "range": (10, 10),
            "items": [["diamond", "diamond"]],
            "chests": [[], [(5, 64, 0)]],
        }
        self.manager.current_ms = self.data
        self.source = FakeSource()

    def test_search_skips_empty_chest_group(self):
        finish_worker(self.manager.search(self.source, {"name": "diamond"}))
        self.effects["highlight_block_by_entity"].assert_called_once_with(
            self.manager.server, 5.0, 64.0, 10, duration=12)
        self.assertIn("command.search.best", self.source.replies)

    def test_search_with_only_empty_groups_reports_no_location(self):
        self.data["chests"] = [[], []]
        finish_worker(self.manager.search(self.source, {"name": "diamond"}))
        self.assertEqual(self.source.replies, ["command.search.nodata"])
        self.effects["highlight_block_by_entity"].assert_not_called()

    def test_search_does_not_report_success_when_highlight_fails(self):
        self.data["range"] = (10, 9)
        finish_worker(self.manager.search(self.source, {"name": "diamond"}))
        self.assertEqual(self.source.replies, ["command.search.nodata"])
        self.effects["highlight_block_by_entity"].assert_not_called()

    def test_highlight_rejects_invalid_indices_without_effects(self):
        for x, y in [(0, 2), (0, -1), (9999, 1), (-1, 1), (0.5, 1), (0, 1.0), (0, 0)]:
            with self.subTest(x=x, y=y):
                self.assertFalse(self.manager.highlight_handler(self.data, x, y))
        self.assertFalse(self.manager.highlight_handler(None, 0, 1))
        self.effects["highlight_block_by_entity"].assert_not_called()
        self.effects["highlight_block_multi"].assert_not_called()

    def test_highlight_command_replies_error_for_invalid_indices(self):
        for x, y in [(0, 2), (0, -1), (9999, 1), (0, 0)]:
            with self.subTest(x=x, y=y):
                self.source.replies.clear()
                finish_worker(self.manager.highlight(self.source, {"name": "diamond", "x": x, "y": y}))
                self.assertEqual(len(self.source.replies), 1)
                self.assertIn("无法高亮", self.source.replies[0])
        self.effects["highlight_block_by_entity"].assert_not_called()

    def test_highlight_command_reports_success_after_starting_highlight(self):
        self.effects["highlight_block_by_entity"].side_effect = lambda *a, **kw: self.assertEqual(self.source.replies, [])
        finish_worker(self.manager.highlight(self.source, {"name": "diamond", "x": 0, "y": 1}))
        self.assertEqual(self.source.replies, ["command.highlight.success"])

    def test_search_keeps_snapshot_when_loaded_storage_changes(self):
        entered = threading.Event()
        resume = threading.Event()
        original = self.manager.hopper_matcher.normalize_item_name
        calls = []

        def normalize(name):
            calls.append(name)
            if len(calls) == 2:
                entered.set()
                if not resume.wait(2):
                    raise AssertionError("snapshot test was not resumed")
            return original(name)

        with mock.patch.object(self.manager.hopper_matcher, "normalize_item_name", side_effect=normalize):
            worker = self.manager.search(self.source, {"name": "diamond"})
            self.assertTrue(entered.wait(2))
            self.manager.current_ms = {
                "name": "other", "axis": "z", "range": (90, 90),
                "items": [["dirt", "dirt"]], "chests": [[(90, 20, 80)], [(90, 30, 70)]],
            }
            resume.set()
            finish_worker(worker)
        self.effects["highlight_block_by_entity"].assert_called_once_with(
            self.manager.server, 5.0, 64.0, 10, duration=12)
        self.assertEqual(self.manager.highlight_flag, "diamond")

    def test_highlight_handler_uses_snapshot_even_after_unload(self):
        self.manager.current_ms = None
        self.assertTrue(self.manager.highlight_handler(self.data, 0, 1, "diamond"))
        self.effects["highlight_block_by_entity"].assert_called_once_with(
            self.manager.server, 5.0, 64.0, 10, duration=12)

    def test_create_admission_is_atomic_and_rejected_task_cannot_release_it(self):
        coordinate_entered = threading.Event()
        resume = threading.Event()
        first_check = threading.Event()
        competing_check = threading.Event()
        main_thread = threading.current_thread()

        class PausingManager(MainStorageManager):
            def __getattribute__(self, name):
                value = super().__getattribute__(name)
                if name == "has_task" and not value and threading.current_thread() is not main_thread:
                    if first_check.is_set():
                        competing_check.set()
                    else:
                        first_check.set()
                        # 模拟检查为 False 后被调度切走；有锁时竞争线程无法读到 False。
                        competing_check.wait(0.2)
                return value

        self.manager.__class__ = PausingManager

        def coordinates(player):
            coordinate_entered.set()
            if not resume.wait(2):
                raise AssertionError("create test was not resumed")
            return types.SimpleNamespace(x=0, y=64, z=0)

        with mock.patch.object(main_storage_module, "get_player_coordinate", side_effect=coordinates):
            with mock.patch.object(self.manager.ms_creator, "create") as create:
                first = self.manager.create(self.source, {"name": "one"})
                self.assertTrue(first_check.wait(2))
                second_source = FakeSource()
                second = self.manager.create(second_source, {"name": "two"})
                self.assertTrue(coordinate_entered.wait(2))
                finish_worker(second)
                self.assertFalse(competing_check.is_set())
                self.assertEqual(second_source.replies, ["command.add.has_task"])
                self.assertTrue(self.manager.has_task)
                create.assert_not_called()
                resume.set()
                finish_worker(first)
                create.assert_called_once()
        self.assertFalse(self.manager.has_task)

    def test_create_failure_releases_admission(self):
        with mock.patch.object(main_storage_module, "get_player_coordinate", side_effect=RuntimeError("boom")):
            worker = self.manager.create(self.source, {"name": "test"})
            with self.assertRaisesRegex(RuntimeError, "boom"):
                finish_worker(worker)
        self.assertFalse(self.manager.has_task)
        self.assertEqual(self.source.replies, ["command.add.error"])


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
