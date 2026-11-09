import time, difflib, os, re
import threading
from main_storage_searcher.utils.highlight_utils import *
from main_storage_searcher.utils.display_utils import rtr, rtr_minecraft, help_msg
from main_storage_searcher.utils.pos_utils import DynamicPos
from main_storage_searcher.utils.config_utils import Config
from main_storage_searcher.utils.hopper_matcher import HopperMatcher
from main_storage_searcher.utils.main_storage_utils import MainStorageCreator, MainStorageData
from mcdreforged.api.all import PluginServerInterface, CommandSource, CommandContext, new_thread, SimpleCommandBuilder, Text, Integer, Info, RTextList, RText, RAction
from minecraft_data_api import get_player_coordinate


class MainStorageManager:

    def __init__(self, server: PluginServerInterface, config: Config) -> None:
        self.has_task = False
        self._task_lock = threading.Lock()
        self.ms_creator = MainStorageCreator(server)
        self.current_ms : MainStorageData = self.ms_creator.load_ms_data(default) if (default := config.main_storage.default) is not None else None 
        self.server = server
        self.config = config
        self.highlight_flag = None
        self.hopper_matcher = HopperMatcher()

        builder = SimpleCommandBuilder()
        builder.arg("name", Text)
        builder.command("!!ms", lambda source, context: source.reply(RTextList(*[rtr("command.help.info", False)]+["\n"+help_msg(command) for command in ["create", "search", "list", "load", "unload", "reload", "setdefault"]])))
        builder.command("!!ms create <name>", self.create)
        builder.command("!!ms create", lambda source, context: source.reply(help_msg("create", "<name> ")))
        builder.command("!!ms search <name>", self.search)
        builder.command("!!ms search", lambda source, context: source.reply(help_msg("search", "<name> ")))
        builder.command("!!ms list", self.list_ms)
        builder.command("!!ms load <name>", self.load)
        builder.command("!!ms load", lambda source, context: source.reply(help_msg("load", "<name> ")))
        builder.command("!!ms unload", self.unlaod)
        builder.command("!!ms reload", self.reload)
        builder.command("!!ms setdefault", self.set_default)
        builder.arg("x", Integer)
        builder.arg("y", Integer)
        builder.command("!!ms highlight <name> <x> <y>", self.highlight)
        builder.register(server)

        server.register_help_message("!!ms", rtr("command.help.basic", False))
    
    def on_info(self, server, info: Info):
        if self.has_task and not info.is_player:
            self.ms_creator.on_info(server, info)

    @new_thread("ms-creator")
    def create(self, source: CommandSource, context: CommandContext):
        if not source.is_player:
            return
        if not source.has_permission(self.config.permission.create):
            source.reply(rtr("command.permission_denied"))
            return
        # 检查与占用必须原子完成，拒绝的执行路径不能释放别人的任务资格。
        with self._task_lock:
            if self.has_task:
                source.reply(rtr("command.add.has_task"))
                return
            self.has_task = True
        try:
            pos = get_player_coordinate(source.player)
            self.ms_creator.create((int(pos.x), int(pos.y), int(pos.z-1)), context["name"])
        except Exception as e:
            source.reply(rtr("command.add.error", e=e))
            raise
        finally:
            with self._task_lock:
                self.has_task = False
    
    def load(self, source: CommandSource, context: CommandContext):
        if not source.has_permission(self.config.permission.load):
            source.reply(rtr("command.permission_denied"))
            return
        name = context["name"]
        if self.current_ms and self.current_ms["name"] == name:
            source.reply(rtr("command.load.exist", name=name))
            return
        self.current_ms = self.ms_creator.load_ms_data(name)
        if self.current_ms:
            source.reply(rtr("command.load.success", name=name))
            return
        source.reply(rtr("command.load.notfound", name=name))
    
    def reload(self, source: CommandSource, context: CommandContext):
        if not source.has_permission(self.config.permission.reload):
            source.reply(rtr("command.permission_denied"))
            return
        if not self.current_ms:
            source.reply(rtr("nodata"))
            return
        name = self.current_ms["name"]
        try:
            data = self.ms_creator.load_ms_data(name)
        except Exception as error:
            source.reply(rtr("command.add.error", e=error))
            return
        if data is None:
            source.reply(rtr("command.load.notfound", name=name))
            return
        self.current_ms = data
        source.reply(rtr("command.reload.success", name=name))
    
    def unlaod(self, source: CommandSource, context: CommandContext):
        if not source.has_permission(self.config.permission.unload):
            source.reply(rtr("command.permission_denied"))
            return
        if not self.current_ms:
            source.reply(rtr("nodata"))
            return
        name=self.current_ms["name"]
        self.current_ms = None
        source.reply(rtr("command.unload.success", name=name))
    
    @new_thread("ms-searcher")
    def search(self, source: CommandSource, context: CommandContext):
        if not source.has_permission(self.config.permission.search):
            source.reply(rtr("command.permission_denied"))
            return
        current_ms = self.current_ms
        if not current_ms:
            source.reply(rtr("nodata"))
            return
        name = context["name"]
        normalized_name = self.hopper_matcher.normalize_item_name(name)
        target = []
        target_index = 0
        best = {"similarity": -1, "pos": (0, 0), "item": "", "target_index": -1}
        for slice_index, items in enumerate(current_ms["items"]):
            for chest_index, item in enumerate(items):
                if item is None or chest_index >= len(current_ms["chests"]) or not current_ms["chests"][chest_index]:
                    continue
                normalized_item = self.hopper_matcher.normalize_item_name(item)
                similarity = difflib.SequenceMatcher(
                    None,
                    normalized_name,
                    normalized_item,
                ).ratio()
                if similarity < 0.5 and normalized_name not in normalized_item:
                    continue
                if similarity > best["similarity"]:
                    best["similarity"] = similarity
                    best["pos"] = (slice_index, chest_index)
                    best["item"] = item
                    best["target_index"] = target_index
                target.append((item, (slice_index, chest_index)))
                target_index += 1
        if best["similarity"] < 0:
            source.reply(rtr("command.search.nodata"))
            return
        target.pop(best["target_index"])
        if not self.highlight_handler(current_ms, best["pos"][0], best["pos"][1], best["item"]):
            source.reply(rtr("command.search.nodata"))
            return
        source.reply(rtr("command.search.best", item=best["item"]))
        source.reply(RTextList(rtr("command.search.others_1"), *[RText(f"§3{item} ").c(RAction.run_command, f"!!ms highlight {item} {pos[0]} {pos[1]}") for item, pos in target], rtr("command.search.others_2", False)))

    
    @new_thread("ms-highlight")
    def highlight(self, source: CommandSource, context: CommandContext):
        if not source.has_permission(self.config.permission.highlight):
            source.reply(rtr("command.permission_denied"))
            return
        current_ms = self.current_ms
        if not current_ms:
            source.reply(rtr("nodata"))
            return
        if self.highlight_handler(current_ms, context["x"], context["y"], context["name"]):
            source.reply(rtr("command.highlight.success", item=context["name"]))
        else:
            source.reply(RTextList(
                rtr("command.search.nodata"),
                RText("（无法高亮：索引必须是范围内的非负整数，且对应箱子组不能为空。）"),
            ))
        
    
    def highlight_handler(self, current_ms, slice_index, chest_index, item=None) -> bool:
        if current_ms is None:
            return False
        # 命令和搜索共用同一入口校验，负数不能被 Python 当作倒数索引。
        if (
            type(slice_index) is not int or type(chest_index) is not int
            or not 0 <= slice_index < current_ms["range"][1] - current_ms["range"][0] + 1
            or not 0 <= chest_index < len(current_ms["chests"])
        ):
            return False
        slice_pos = current_ms["range"][0] + slice_index
        chests = current_ms["chests"][chest_index]
        if not chests:
            self.server.logger.warning(
                "Chest group at slice %s, index %s is empty; skip highlight.",
                slice_index,
                chest_index,
            )
            return False
        self.highlight_flag = item
        # 整次高亮只读调用方快照，避免 load/reload/unload 中途切换定位数据。
        main_axis = "x" if current_ms["axis"] == "z" else "z"
        chest_pos = (sum(DynamicPos(chest) for chest in chests)/len(chests)).set_axis(slice_pos, main_axis)
        highlight_block_by_entity(self.server, *chest_pos, duration=12)
        prompt_slice = [DynamicPos(pos).set_axis(slice_pos, main_axis) for chest in current_ms["chests"] for pos in chest]
        # 5 轮重刷共约 10 秒，必须放到后台线程，否则 search / highlight 命令
        # 要等全部刷完才能回复玩家。
        self._run_highlight_prompt(prompt_slice, item)
        return True

    @new_thread("ms-highlight-prompt")
    def _run_highlight_prompt(self, prompt_slice, item):
        """反复重刷红色玻璃提示，直到被新的高亮顶掉或刷满 5 轮。"""
        for _ in range(5):
            highlight_block_multi(self.server, prompt_slice, block="red_stained_glass", wait=0.3)
            time.sleep(2)
            if self.highlight_flag != item:
                break
    

    def set_default(self, source: CommandSource, context: CommandContext):
        if not source.has_permission(self.config.permission.setdefault):
            source.reply(rtr("command.permission_denied"))
            return
        current_ms = self.current_ms
        mscfg = self.config.main_storage
        
        if not current_ms:
            if mscfg.default:
                mscfg.default = None
                self.server.save_config_simple(self.config, "config.json")
                source.reply(rtr("command.setdefault.unset"))
            else:
                source.reply(rtr("command.setdefault.exist_unset"))
            return
        name = current_ms["name"]
        if mscfg.default != name:
            mscfg.default = name
            self.server.save_config_simple(self.config, "config.json")
            source.reply(rtr("command.setdefault.success", name=name))
        else:
            source.reply(rtr("command.setdefault.exist", name=name))
    
    def list_ms(self, source: CommandSource, context: CommandContext):
        if not source.has_permission(self.config.permission.list):
            source.reply(rtr("command.permission_denied"))
            return
        current_ms = self.current_ms["name"] if self.current_ms else None
        default = self.config.main_storage.default
        source.reply(rtr("command.list.info", False))
        flag = False
        for file in os.listdir(self.server.get_data_folder()):
            if (result := re.fullmatch(r"msdata-(?P<name>.+)\.json", file)) is not None:
                msg = ms_name = result.group("name")
                if ms_name == current_ms and ms_name == default:
                    msg = rtr("command.list.current_and_default", False, name=ms_name)
                elif ms_name == current_ms:
                    msg = rtr("command.list.current", False, name=ms_name)
                elif ms_name == default:
                    msg = rtr("command.list.default", False, name=ms_name)
                else:
                    msg = rtr("command.list.plain", False, name=ms_name)
                source.reply(msg)
                flag = True
        if not flag:
            source.reply(rtr("command.list.empty", False))
