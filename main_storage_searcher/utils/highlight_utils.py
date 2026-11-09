from mcdreforged.api.all import ServerInterface, new_thread
import time
from typing import List, Tuple, Callable, Any, Dict, Optional


def highlight_block_clear(server: ServerInterface, tag="mark"):
    server.execute(f"kill @e[tag={tag}]")

@new_thread("highlight-block")
def highlight_block(server: ServerInterface, x, y, z, new=True, block="gray_stained_glass", tag="mark", temp=1):
    # Since Minecraft 26.1 the BlockState field of falling_block takes the new
    # data-component form (a plain block-state id string). The old
    # {Name:"minecraft:..."} compound is silently dropped, which yields an
    # invisible/air entity, so use the string form.
    nbt = '''{Tags:["%s"],Glowing:1b,Invisible:1b,Invulnerable:1b,PersistenceRequired:1b,Silent:1b,NoGravity:1b,Time:%s,DropItem:0b,HurtEntities:0b,BlockState:"minecraft:%s"}'''%(tag, temp, block)
    if new:
        server.execute(f"kill @e[tag={tag}]")
    server.execute(f"summon minecraft:falling_block {int(x)} {int(y)} {int(z)} {nbt}")

@new_thread("highlight-block-multi")
def highlight_block_multi(server: ServerInterface, multi_pos: List[Tuple[int, int, int]], new=True, block="gray_stained_glass", tag="mark", temp=0, wait=0):
    id = time.time()
    if new:
        server.execute(f"kill @e[tag={tag}]")
    for pos in multi_pos:
        x, y, z = pos
        highlight_block(server, x, y, z, new=False, block=block, tag=tag if wait == 0 else f"temp{id}", temp=temp)
    if wait > 0:
        time.sleep(wait)
        server.execute(f"kill @e[tag=temp{id}]")

@new_thread("highlight-block-multi")
def highlight_block_multi_steps(server: ServerInterface, multi_group_pos: List[List[Tuple[int, int, int]]], block="gray_stained_glass", wait=0.05, end_func: Tuple[Callable, Optional[Tuple[Any]], Optional[Dict[str, Any]]]=None):
    tag = f"step{time.time()}"
    for group_pos in multi_group_pos:
        highlight_block_multi(server, group_pos, block=block, tag=tag)
        time.sleep(wait)
    highlight_block_clear(server, tag)
    if end_func:
        args, kwargs = end_func[1] if len(end_func)>=2 else (), end_func[2] if len(end_func)>=3 else {}
        end_func[0](*args, **kwargs)

@new_thread("highlight-block-timer")
def highlight_block_timer(server: ServerInterface, x, y, z, block="gray_stained_glass", wait=1):
    id = time.time()
    highlight_block(server, x, y, z, new=False, block=block, tag=f"temp{id}", temp=0)
    time.sleep(wait)
    server.execute(f"kill @e[tag=temp{id}]")

@new_thread("highlight-block-by-entity")
def highlight_block_by_entity(server: ServerInterface, x, y, z, new="highlight_entity", tag=[], duration: int = 0):
    if duration > 0:
        tag.append(f"highlight_entity{time.time()}")
    if new:
        tag.append(new)
        server.execute(f"kill @e[tag={new}]")
    tag = ",".join(tag)
    # ActiveEffects uses the string effect id form since Minecraft 1.20.5
    # ({Id:14,...} is silently ignored on modern servers, so the marker slime
    # would not glow).
    nbt = '''{Tags:[%s],Glowing:1b,NoAI:1b,Invulnerable:1b,PersistenceRequired:1b,CanPickUpLoot:0b,Silent:1b,NoGravity:1b,Size:0,DeathLootTable:"entities/empty",ActiveEffects:[{id:"minecraft:glowing",amplifier:0,duration:199980}],DeathTime:19b}'''%tag
    server.execute(f"summon slime {x} {y} {z} {nbt}")
    if duration:
        time.sleep(duration)
        server.execute(f"kill @e[tag={new}]")
    