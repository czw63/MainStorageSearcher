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