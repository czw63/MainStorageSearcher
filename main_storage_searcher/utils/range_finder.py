from typing import List, Tuple

from mcdreforged.api.all import PluginServerInterface

from main_storage_searcher.utils.block_utils import BlockDataGetter
from main_storage_searcher.utils.highlight_utils import highlight_block_timer
from main_storage_searcher.utils.hopper_matcher import HopperMatcher
from main_storage_searcher.utils.pos_utils import DynamicPos


class RangeFinder:
    """Find the start and end positions along the main storage axis."""

    def __init__(
        self,
        server: PluginServerInterface,
        api: BlockDataGetter,
        matcher: HopperMatcher,
    ) -> None:
        self.server = server
        self.api = api
        self.matcher = matcher

    def find_range(
        self,
        hoppers: List[Tuple[int, int, int]],
        axis: str,
    ) -> Tuple[int, int]:
        """Return the inclusive storage range for the detected slice axis."""
        if not hoppers:
            raise ValueError("Cannot find a storage range without hoppers.")

        hoppers = [
            pos for pos in hoppers
            if (axis == "z" and pos[0] == hoppers[0][0])
            or (axis == "x" and pos[2] == hoppers[0][2])
        ]
        if not hoppers:
            raise ValueError(f"No hoppers found for storage axis {axis!r}.")
        offset_axis = "x" if axis == "z" else "z"
        start_pos = DynamicPos(hoppers[0])
        bounds = []
        for direction in (-256, 256):
            bounds.append(self._find_boundary(start_pos, offset_axis, direction))

        start, end = sorted(bounds)
        return start, end

    def _find_boundary(
        self,
        start_pos: DynamicPos,
        offset_axis: str,
        search_step: int,
    ) -> int:
        """Find the outermost hopper by exponential expansion followed by bisection."""
        direction = 1 if search_step >= 0 else -1
        near = 0
        remote = None

        # 指数扩展直到第一次遇到非漏斗，避免固定 ±256 把更大的全物品截断。
        step = 1
        while step <= 4096:
            offset = direction * step
            pos = start_pos.offset_axis(offset, offset_axis)
            highlight_block_timer(self.server, *pos, wait=0.2)
            block_data = self.api.get_block_data(*pos)
            if block_data is not None and self.matcher.is_hopper_block(block_data["data"]):
                near = offset
                step *= 2
            else:
                remote = offset
                break

        if remote is None:
            pos = start_pos.offset_axis(near, offset_axis)
            return int(pos[2] if offset_axis == "z" else pos[0])

        while abs(remote - near) > 1:
            mid = near + (remote - near) // 2
            pos = start_pos.offset_axis(mid, offset_axis)
            highlight_block_timer(self.server, *pos, wait=0.2)
            block_data = self.api.get_block_data(*pos)
            if block_data is not None and self.matcher.is_hopper_block(block_data["data"]):
                near = mid
            else:
                remote = mid

        pos = start_pos.offset_axis(near, offset_axis)
        return int(pos[2] if offset_axis == "z" else pos[0])
