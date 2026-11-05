# McBridge（Fabric 客户端模组）

让「大肥鱼直播姬」通过 `mc-bridge/v1` **观察并操作你本机的正版 Java 版角色**。

- 只绑定 `127.0.0.1:8092`，不联机、不碰账号、不改存档；
- 不注入键鼠事件：移动/挖掘/攻击/放置都走游戏自己的按键状态和交互接口；
- 单人游戏默认失焦就暂停，模组在第一次收到动作时把 `pauseOnLostFocus` 设为
  `false`（只改运行时选项，不写 `options.txt`），这样游戏最小化/切后台也能继续跑；
- 客户端渲染线程安全：HTTP 线程只把任务丢回客户端主线程，超时 4s。

## 支持的动作

| 动作 | 说明 |
| --- | --- |
| `state`（GET） | 屏幕/世界时间/血量/饥饿/背包/附近方块/生物/简易庇护判定 |
| `start` / `stop` / `reset` | `start` 关掉暂停界面；`stop` 松开所有按键；`reset` 不支持（真机不能重置世界） |
| `goto` | `target=home` 或 `target=<方块/生物名>`，也可直接给 `x`/`z` |
| `mine` | 走到方块 4 格内，看向它并持续挖掘，直到消失或 20s 超时 |
| `attack` | 走到目标 2.2 格内，看向并攻击，直到死亡或 20s 超时 |
| `place` | 在脚边 3 格范围内找空位放下当前快捷栏物品 |
| `eat` | 选快捷栏里的食物并使用 |
| `equip` | 切到快捷栏里指定物品 |
| `wait` | 最多 20s |

> `craft` 已接入：打开背包（2×2）或工作台（3×3），从配方书找到目标配方、放置材料、
> 取产物。`smelt`（熔炉界面）**暂未实现**，会明确返回 `ok:false`；真机跑 `local` 时
> 会自动跳过熔炼（生肉直接吃、铁先不烧）。

## 构建

需要 JDK 25（微软商店版启动器自带的 `runtime/java-runtime-epsilon` 就是完整 JDK，
含 `javac`）。仓库自带 Gradle wrapper，第一次会下载 Gradle 9.8.2：

```powershell
$env:JAVA_HOME = "$env:APPDATA\.minecraft\runtime\java-runtime-epsilon"
$env:PATH = "$env:JAVA_HOME\bin;$env:PATH"
.\gradlew.bat --no-daemon build
```

首次构建 Loom 会从 Mojang/Fabric 下载 26.2 客户端和库（数百 MB），产物：

```
build/libs/mcbridge-0.1.0.jar
```

## 安装

1. 启动器里先登录并**启动一次 26.2**（`versions/26.2` 目前只有 json，没有客户端 jar），
   进一个世界后退回主菜单；
2. 装 Fabric Loader 0.20.1（ installer：`fabric-installer` 的 `client` 子命令，
   `-mcversion 26.2 -loader 0.20.1`）；
3. 把 `mcbridge-0.1.0.jar` 放进 `%APPDATA%\.minecraft\mods\`；
4. 启动 26.2（Fabric 配置），进世界后：

```powershell
$env:MINECRAFT_BRIDGE = "http"
python main.py game state
```

返回里 `source` 应为 `http`，`ready=true`。

## 已知边界

- 26.2 是新版本，Mojang 映射可能随版本变；换版本要改 `gradle.properties` 并重新构建；
- 熔炼未实现；合成依赖配方书里已解锁的配方，材料不够会返回明确错误；
- 「庇护」是近似判定（头顶 + 四周 2 格），和 `rules.py` 的数值不完全一致；
- 单人存档暂停时 HTTP 线程仍在，但游戏世界不 tick，动作会等到恢复；
- 模组会把 `pauseOnLostFocus` 关掉（只影响运行时），这样后台/最小化也能操作。
