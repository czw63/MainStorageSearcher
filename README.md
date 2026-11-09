# MainStorageSearcher

中文 | [English](./README_en.md)

一个MCDR插件，帮助你在全物品中寻找指定物品~

## 命令

`!!ms` 查看命令帮助

`!!ms create <name>` 识别全物品样式，目前支持常规非混储非编码全物品。详见[识别全物品](#识别全物品)

`!!ms search <name>` 在加载的全物品样式中搜索指定物品并高亮

`!!ms setdefault` 将当前加载的全物品样式设为默认加载，若未加载则取消默认加载

`!!ms list` 列出存储的全物品样式

`!!ms load <name>` 加载全物品样式

`!!ms reload` 重载全物品样式

`!!ms unload` 卸载已加载的全物品样式

## 依赖

**Python**

- `python >= 3.10`

**插件**

- `minecraft_data_api`

## 版本兼容（Minecraft 26.x 适配）

本仓库是 [`LazyAlienServer/MainStorageSearcher`](https://github.com/LazyAlienServer/MainStorageSearcher) 的 fork，
适配 **Minecraft Java 26.3 / 26.x**（协议 777，数据包 121.0），同时保持旧版本（1.20.5+ ~ 1.21.x）可用。

针对 26.x 的改动：

- **命令回显前缀**：26.1 起控制台命令反馈带 `System chat: ` 前缀，`/data get block` 与
  `execute if block` 的回显解析现在先剥前缀；旧版本没有前缀也照常匹配。
- **gamerule 命名**：26.1 把所有 gamerule 改成 snake_case（`sendCommandFeedback` →
  `send_command_feedback`）。插件两条都发，跑在哪个版本都能用。
- **`falling_block` 的 `BlockState`**：26.1 起数据组件化，`{Name:"minecraft:x"}` 会被静默丢弃
  导致高亮实体变成空气，改用字符串形式 `BlockState:"minecraft:x"`。
- **`ActiveEffects`**：改用字符串效果 id（`{id:"minecraft:glowing",...}`），旧写法在现代版本
  会被忽略，标记史莱姆不发光。
- **重命名物品识别**：1.20.5+ 起物品名在 `components` 里（`minecraft:custom_name`），
  不再只查 `tag.display.name`，重命名物品仍会被正确跳过。
- **坐标边界二分**：修掉负方向取整导致的边界偏移（`range` 起点少 1，多出一个空单片）。
- **超时兜底**：命令无回显时不再带崩整个识别流程；`execute if block` 遇到非法属性值时
  按"不匹配"处理，而不是干等 5 秒超时。
- 官方 ToDo 里的 `en_us` 语言文件仍未提供，**MCDR 的 `language` 请设成 `zh_cn`**，
  否则界面会显示原始翻译键。

实测环境：Minecraft **26.3** + MCDReforged **2.16.2** + MinecraftDataAPI **1.6.2**，
3 个单片的测试全物品（48 个漏斗 / 48 个箱子 / 48 种物品），`!!ms create` 完整识别，
`axis`、`range`、`hoppers`、`chests`、`items` 全部正确，耗时约 9 秒。

## 0.4.1 的健壮性修复

0.4.0 之后对整个插件做了一遍静态审查（13 个文件 1156 行，另有一份独立的 codex 交叉复核），
下面这些是与版本无关、但会让识别结果错掉且**不报任何错**的硬伤，0.4.1 全部修掉：

- **`!!ms create` 失败后 `send_command_feedback` 永久卡在 `false`**：`create()` 一进来就关命令反馈，
  但只有走到最后一步的异步回调才恢复。中途任何异常（找不到轴、范围判定失败、超时）都会让
  **全服玩家失去命令回显**。现在整段包在 `try/finally` 里，异常也一定恢复。
- **`items` 与 `chests` 索引错位**：批量取数接口原来只收集成功的响应、丢掉"空气/未加载"的位置，
  返回列表比输入短，调用方却按位置一一对应。只要有一个漏斗读失败，后面所有物品整体左移一格，
  搜索会高亮到**错的箱子**，而且全程无报错。现在返回列表长度严格等于输入长度，缺的填 `None`。
- **批量取数实际是 N 次串行 5 秒超时**：一行 33 个位置最坏要等 165 秒。现在整批共享一个
  `max(5, N×0.5)` 秒的 deadline，超时未收齐的填 `None` 并记一条 warning。
- **`task_count` 竞态**：超时路径（主调线程）和回显路径（MCDR 线程）无锁并发读改写，判断又用 `== 0`，
  可降到负数后继续把无关的服务端消息当数据解析。现在全部加锁、减法保底、判断改 `<= 0`。
- **`range` 边界被截在 ±255**：原来固定从 ±256 开始二分，且指数扩展那步被下一轮覆盖，等于没扩展。
  连续范围达到 256 片时最外一片会丢。现在改成真正的指数扩展（±1、±2、±4…）+ 二分，上限 4096。
- **扫描和边界探测混用"漏斗里正好 5 个物品"的严格判断**：真实全物品里只要有一个漏斗被取走物品、
  不满 5 格，它就被当成"不是漏斗"，导致整排漏检或范围收缩。现在拆成两个判断：扫描仍用严格版
  （用来排除空的运输漏斗），边界探测用只认方块 id 的宽松版。
- **一个漏斗找不到箱子就丢弃整份箱子数据**：原来 `_find_all_chests` 吞掉异常返回空列表，
  然后照样保存、照样广播"创建完成"。现在逐漏斗容错并保留空组占位（长度仍与漏斗对齐），
  只有**全部**找不到才判定失败；搜索命中空组时跳过，不影响其他数据。
- **高亮实体 tag 列表无限增长**：`highlight_block_by_entity` 的 `tag=[]` 是可变默认参数，
  每次搜索都往同一个列表里追加，NBT 越来越长（最终可能撞命令长度上限），而且延时清理会误杀
  之后新建的高亮。现在改成 `None` 默认值并在函数内拷贝，清理只杀本次的唯一 tag。
- **`!!ms highlight` 阻塞约 10 秒**：它有 5 次 `sleep(2)` 的循环却没加 `@new_thread`，
  会卡住命令回调。现已加上。
- **漏斗成环导致递归爆栈**：`find_target_chest` 加了 `visited` 环检测和 64 层深度上限。
- 另外修了三处小的：`reload` 不检查 `load_ms_data()` 返回 `None`（文件被删了还回"重载成功"）、
  `highlight_handler` 在空箱子组上会 `ZeroDivisionError`、`get_multi_block_data` 的 `path` 参数
  收了却没拼进命令。

仓库根目录的 `test_logic.py` 是配套的单元测试（9 个用例），用 `sys.modules` 打桩伪造
`mcdreforged` / `minecraft_data_api`，**不需要启动服务器**：

```bash
python3 -m unittest test_logic -v
```

覆盖：范围边界的指数扩展 + 二分（±300 片场景）、批量取数的位置对齐（一半位置无回显）、
`task_count` 混合调用后不为负、严格/宽松漏斗判断的差异、部分/全部找不到箱子的行为。

## 识别全物品

完整原理说明参见[原理](/docs/theory.md)

站在全物品普通单片正中央的通道上使用`!!ms create <name>`后，插件会检测玩家`(x ± 16, y-5 ~ y+16, z ± 16)`的范围，搜索*五个槽位都有物品且第一个槽位的物品数<64，且上方不是漏斗的*漏斗，并按照物品可能的传输顺序找到对应的存储箱子，自动完成全物品识别。

然后，一个存储了这个全物品信息的文件会被保存在`/config/main_storage_searcher/msdata-<name>.json`中。

使用`!!ms load <name>`以加载你刚刚创建的全物品

## ToDo

- [ ] `en_us`语言文件
- [ ] 识别并加载大宗，创建全物品样式组`group`
- [ ] 更优的提示和回显
- [x] 命令权限限制
- [x] 命令帮助
- [x] 自动加载默认全物品配置