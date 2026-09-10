# MikoType

[English](README.md) | [简体中文](README.zh-CN.md)

MikoType 是一个面向 VR 的实体键盘视觉定位与交互管线。系统使用 MediaPipe
追踪手部，通过少量 ArUco Marker 建立键盘参考平面，将指尖映射到用户校准后
的键位，生成自适应 3D 键盘，并向 VR 消费端发布经过版本校验的场景数据。

> **当前开发方式：**先在 Mac 本机运行共用的生产视觉/映射代码和完整键盘 WebUI，
> 完成非 VR 部分的开发与验证。最终 VR 部署仍面向单台 Windows 电脑；SteamVR
> Home 接入和头显验收独立为 Windows 模块。Mac 流程不依赖 SteamVR，不需要连接
> Windows，也没有启用分布式推理。

Python 分发包与旧命令为了兼容仍使用 `vr-desk-vision` 和 `deskvision`。从源码
启动时，uv 和 Conda 都使用 [`run_mikotype.py`](run_mikotype.py)。该入口会优先
加载并校验脚本所在仓库的 `src/`，避免旧 `mikotype` 命令或其他目录的 editable
安装被误用；依赖仍由当前 Python 环境提供。

## 已实现功能

- OpenCV 只保留最新帧的视频采集，支持 macOS AVFoundation、Windows MSMF、
  DSHOW 和自动选择后端，并可在 WebUI 选择摄像头。
- MediaPipe 双手 21 点追踪。
- 使用 `DICT_4X4_50` 稀疏 Marker 建立键盘参考坐标系。
- 用户键位清单、每键五次触点校准和完整版本一致性检查。
- 指尖 Bubble、可能直接接触的键位和按距离渐弱的周围键高亮。
- 每个键对应独立 `key:<physical_key_id>` 节点的自适应 GLB。
- 本机 FastAPI 检查界面、模型接口和 `SceneState 0.2` WebSocket。
- 独立 Windows OpenVR GenericTracker 驱动、实时 Overlay bridge，支持当前用户
  键盘模型导出，以及 `/steamvr` 空间对齐、状态和日志观测页。

系统输出的是“可能接触的键位”，不会将高亮声明为机械按压，也不会注入操作
系统键盘输入。

## V0.1 架构

```text
同一台 Mac（视觉/映射验证）或 Windows 电脑

摄像头
  -> 最新 FramePacket
  -> MediaPipe 手部 + ArUco 键盘位姿
  -> 校准后的候选键与高亮
  -> 本机 SceneState + 自适应键盘 GLB
  -> 127.0.0.1 FastAPI
  -> 浏览器：参数、校准、手部、键位状态高亮
     + 可下载的自适应 3D 键盘 GLB

独立的 Windows 验收模块（视觉服务不会自动启动）：
  本机 FastAPI -> Windows 实时 bridge -> GenericTracker 3D 键盘
                                     -> 平面 Overlay 高亮 + 指尖
  WebUI /steamvr <- bridge 日志 + 手动收集 SteamVR 日志
  （手动房间空间对齐；需要 Windows / 头显实机验收）
```

所有实时视觉阶段使用同一个采集帧。下游变慢时会跳过已被替代的帧，不会堆积
延迟队列。V0.1 的本地服务被限制为回环地址，因此画面和状态不会离开本机。

## macOS 本机开发与验证

先通过此流程测试摄像头、MediaPipe 手部、Marker 定位、完整键盘设置、键位高亮
与自适应 GLB 生成，再到 Windows 做硬件测试。Mac 与 Windows 使用同一份 `src/deskvision`
生产代码、Python 3.12 和锁定依赖，不需要启动旧 Demo 服务。

在仓库目录下的终端执行：

```bash
uv sync --locked --python 3.12 --extra test
uv run --locked python ./run_mikotype.py doctor --config configs/macos.yaml
uv run --locked python ./run_mikotype.py run \
  --config configs/macos.yaml \
  --acknowledge-mediapipe-metrics
```

若已激活并用 `python -m pip install -e ".[test]"` 安装好 Conda 环境，省略
`uv run --locked` 前缀即可，不要混用两种环境。macOS 弹出摄像头权限提示时，
允许承载 Python 的应用（例如终端或 Codex）访问；MikoType 不会替你修改系统
隐私设置。同一摄像头若被旧 Demo 使用，请先关闭旧 Demo。

只打开启动后 `OPEN THIS EXACT URL` 打印的**实际地址**，自动端口范围仍为
9000–10000。在这一个控制台内依次完成：

1. `/setup`：先看基础设置总览，点击“开始教程”，按摄像头 → 键位图 → Marker
   → 每键五次触点 → 应用 / 重启的顺序设置。已有有效布局和校准可跳过沿用；
   进入教程、切换步骤不会保存、重置或自动开始采样。
2. 摄像头步骤可调整左右镜像、上下翻转预览；`/settings` 提供设备选择和高级
   输入方向矫正。Mac 使用 AVFoundation；摄像头打开失败时仍可选择设备并重试。
   检查实际帧率，而不只看请求 FPS。新校准完成后应用键盘包，并按提示重启。
3. `/`：先观察青色的 MediaPipe 原始手部骨架/指尖，它们不要求画面中有 Marker。
   绿色 Bubble 和键帽高亮是已经映射的指尖/候选键，需要可用的键盘位姿。
   下载生成的 3D 键盘，并遮住 Marker 或将手移出画面，检查过期高亮会清除；
   高亮不代表机械按键触发。

Mac 的生效键盘文件与校准工作区放在 Git 忽略的 `data/local/macos/` 下；首次
执行 `run` 或 `setup` 会用仓库已提交的示例初始化本地键盘，不覆盖已有本地校准。
`check` 是只读检查，不会初始化尚不存在的本地配置产物。
参数单独写入 `configs/macos.local.yaml`。Mac WebUI 不会写入 Windows 配置、
仓库示例键盘、Demo 输出或测试夹具。附带键盘只是初始种子，不能证明另一套
摄像头/键盘已经校准；请为当前实体设备重新注册与采样后再评估键位准确度。

读取摄像头状态不会扫描或打开设备；扫描由用户主动触发，Mac 读取的是
AVFoundation 设备清单。找不到时可以手动输入设备编号。应用切换时追踪会短暂
暂停，并清空此前的帧/状态；新摄像头打开成功后才保存选择，不会删除键盘文件。
专用摄像头选择器可即时应用；普通参数表单内修改的摄像头参数仍需重启。

更换设备/后端后，旧 staging 文件保留供复查，但系统会阻止触点采样和应用键盘包，
直到重新注册并锁定 Marker。持久化的 `camera_binding.json` 也会检测跨重启的
摄像头/配置变化。仅改变相机实际位置无法由配置检测，需要自行复查 Marker reference
与触点准确度。Mac 浏览器中键位状态视图通过，只代表映射管线的验证，不代表
SteamVR Home 可见、头显输入或摄像头到 VR 的空间对齐已通过；后者在 Windows
单独验收。

## Windows 本机启动

环境要求：

- Windows 10/11 x64 和 Git。
- 使用 uv（推荐）或 Conda 管理 Python 3.12 x64。
- OpenCV 可以访问的摄像头。
- 在 Windows 隐私设置中允许桌面应用访问摄像头。
- 使用仓库附带的键盘包，或通过 Setup 流程生成新键盘包。

### 方案 A：uv（推荐）

先通过 WinGet 安装一次 [uv](https://docs.astral.sh/uv/getting-started/installation/)，
然后重新打开 PowerShell：

```powershell
winget install --id=astral-sh.uv -e

git clone https://github.com/miko8422/MikoType.git
cd MikoType

uv sync --locked --python 3.12 --extra test

uv run --locked python .\run_mikotype.py check --config configs\windows.yaml
uv run --locked python .\run_mikotype.py run `
  --config configs\windows.yaml `
  --acknowledge-mediapipe-metrics
```

仓库提交的 [`uv.lock`](uv.lock) 用于复现相同依赖，uv 会自动管理项目内的
`.venv`。

### 方案 B：Conda

安装 Windows [Conda 发行版](https://docs.conda.io/projects/conda/en/stable/user-guide/install/windows.html)
后，打开该发行版自带的 PowerShell Prompt。若要使用普通 PowerShell，请先在前者中
执行一次 `conda init powershell`，再重新打开 PowerShell：

```powershell
git clone https://github.com/miko8422/MikoType.git
cd MikoType

conda create --name mikotype --override-channels --channel conda-forge `
  python=3.12 pip --yes
conda activate mikotype
python -m pip install -e ".[test]"

python .\run_mikotype.py check --config configs\windows.yaml
python .\run_mikotype.py run `
  --config configs\windows.yaml `
  --acknowledge-mediapipe-metrics
```

已有 Conda 环境时，从仓库根目录更新并先核对解释器，再重新安装项目。
`sys.executable` 应指向 `mikotype` Conda 环境；如果不是，先重新打开 Conda
PowerShell Prompt 并激活正确的环境：

```powershell
git pull --ff-only
conda activate mikotype
python -c "import sys; print(sys.executable)"
python -m pip install -e ".[test]"
python .\run_mikotype.py --version
python .\run_mikotype.py doctor --config configs\windows.yaml
python .\run_mikotype.py run `
  --config configs\windows.yaml `
  --acknowledge-mediapipe-metrics
```

本次版本的 `--version` 应显示 `MikoType 0.1.0.dev4`，源码路径应位于当前仓库。
`doctor` 只诊断安装和配置文件；`"status": "ready"` 不代表 Windows 摄像头、
网络或 SteamVR 已验收。入口还会将工作目录设为仓库根目录，因此命令中的相对
路径以仓库根目录为准。若要单独核查旧的裸命令来自哪里，可执行：

```powershell
Get-Command mikotype -All -ErrorAction SilentlyContinue
python -m pip show vr-desk-vision
python -c "import deskvision; print(deskvision.__file__)"
```

`WinError 10048` 表示请求的套接字已被其他进程占用。若 8765 显示 **Pimax Client**，
浏览器访问到的是 Pimax 的服务。旧日志中的 `MikoType running at http://127.0.0.1:8765`
在旧服务真正绑定端口前就会打印，因此不能证明启动成功。再次粘贴时间戳和 PID
完全相同的日志，也不能证明更新后的源码已经运行。请用源码入口重新启动，核对
这次输出的版本、源码路径以及 `OPEN THIS EXACT URL`，据此区分旧安装与新的
端口错误，而不是直接猜测 Windows 使用了哪个环境。

不要混用 uv 和 Conda 环境。后续命令以 uv 写法为准；如果已经激活 Conda
环境，不要再调用 uv：把 `uv run --locked python .\run_mikotype.py <子命令>` 换成
`python .\run_mikotype.py <子命令>`；对于 uv 前缀后本来就是 `python -m ...` 的
命令，只保留 `python -m ...`；测试命令则把
`uv run --locked --extra test pytest -q` 换成 `python -m pytest -q`。Conda 同样
提供环境隔离，但只有推荐的 uv 方案会使用仓库中精确的跨平台依赖锁。

运行时就绪后，终端会打印 `OPEN THIS EXACT URL: ...`。只打开该地址，不要假定
最终使用某个固定端口；默认自动选择范围为 9000–10000。随后一个进程和一路
摄像头会在同一本地控制台提供：

- `/`：严格同帧的视频、手部/键盘状态、质量指标、透视键位状态高亮和自适应
  GLB 下载。当前生产页面不是加载 GLB 本体的 3D 渲染器。
- `/settings`：选择当前摄像头，并校验保存白名单内的摄像头、预览、MediaPipe、
  Marker、交互、流水线和诊断参数。
- `/setup`：基础设置总览与分步教程，也保留各模块直达入口。展示已有配置与
  校准进度，调整键位尺寸、注册 Anchor、采集触点并生成自适应 3D 键盘。

WebUI 参数会原子写入 Git 忽略的 `configs/windows.local.yaml`。摄像头选择操作
可以在当前会话应用设备切换；普通参数编辑器仍会标明哪些变更需要重启。
以界面结果为准，不要把“已保存”理解为全部参数都已在运行中生效。

`run` 和 `setup` 默认在 **9000–10000（包含两端）** 中选择端口。首选端口在范围
内时先尝试它，再从 9000 起尝试区间内其余端口；旧配置或本地覆盖文件中的 8765
等区间外值，在自动模式下会跳过并从 9000 开始选择，不会改写已保存的配置值。
如果发现“相同配置 + 同一 Setup 工作区”的 MikoType，会复用它；否则会在打开
摄像头前独占预留第一个可用端口，并将同一套接字交给 Web 服务。已占用或被
Windows 保留的端口会跳过；整个区间都不可用时，启动会在打开摄像头前失败。

MikoType 不会终止或重新配置占用端口的进程。Pimax 软件及其他任何本机服务都会
保持运行，扫描只会继续尝试下一个端口。身份探测只直接访问回环地址，并仅对这些
请求绕过继承的 HTTP(S) 代理；它不会修改 Windows 代理设置、VPN 状态、路由或
其他应用的网络配置。

始终只打开终端在 `OPEN THIS EXACT URL:` 后打印的地址。如果必须固定使用首选
端口，可显式启用严格模式：

```powershell
uv run --locked python .\run_mikotype.py run `
  --config configs\windows.yaml `
  --port 9500 `
  --strict-port `
  --acknowledge-mediapipe-metrics
```

严格模式会使用显式指定或配置中的端口，包括 9000–10000 以外的端口。如果它
不可用或属于身份不匹配的进程，MikoType 会在打开摄像头前失败。可选的 Windows
SteamVR bridge 单独启动；视觉服务不会自动启动 SteamVR 或修改 VPN/Pimax 设置。

默认摄像头后端是 `msmf`。如果摄像头无法稳定打开，可在参数设置页依次尝试
`dshow`、`any`；选错摄像头时在摄像头面板刷新并选择设备。V0.1 会请求分辨率和 FPS，
但暂不验证所有摄像头驱动是否真正接受了这些参数。

## 键盘校准（Mac/Windows 共用控制台）

主 `run` 命令运行时，从终端打印的准确地址进入控制台并打开其中的 Setup 页面；
不要假定使用某个固定端口，也不要再启动第二个服务。下面的兼容命令只在没有匹配的
MikoType 实例时启动同一个集成控制台；若已经运行，它会打印现有 Setup 的准确
地址：

```powershell
uv run --locked python .\run_mikotype.py setup `
  --config configs\windows.yaml `
  --acknowledge-mediapipe-metrics
```

Setup 会使用现有布局的键位清单和校准顺序，允许用户修正每个键的位置与尺寸、
注册 Marker Anchor、为每个键采集五次右手食指触点、校验全部产物版本，并重建
自适应 GLB。应用完整键盘包后需要重启运行时。V0.1 中新增/删除 key ID、
修改标签或 Marker Anchor 分配仍需手动编辑 Layout 文件。

总览只展示实际读取到的配置、文件与进度，不将文件存在当作实物精度验收通过。
打开页面、切换教程或跳过已保存布局都不会改写校准。保存发生变化的布局、
明确开始新注册时，会使相关 **staging 草稿**失效，界面会先要求确认；
当前生效键盘文件保持不变，直到明确点击“应用”。

摄像头方向分两层：

- **水平镜像预览 / 垂直翻转预览**：视频与叠加显示一起翻转，即时保存，
  不改变算法坐标，不使既有校准失效。
- **高级输入方向矫正**：修改 MediaPipe 和 ArUco 实际接收的图像，仅用于
  摄像头源本身反向的情况。修改后会停用旧映射的指尖与键帽高亮，需重新
  注册 Anchor、采集触点、应用并重启；原始手部仍可观测。软件无法自动判断
  驱动是否已在内部镜像，因此不要仅凭预览方向修改算法输入。

Anchor 现在把采样进度（如 `5/5`）、滚动窗口有效观测数、稳定性和能否锁定
分别展示。累计五次不代表已经稳定；不能锁定时查看对应原因。此修复纠正了
`48/5` 的误导性显示，没有放宽原有稳定性条件，也不要求每帧同时看见六个 Marker。

## Windows 实时 SteamVR / Home 接入

请使用生产接入目录 [`integrations/steamvr`](integrations/steamvr/README.md)，
而不是历史 `demo/steamvr_home_hybrid` 静态烟测服务。按前面的命令启动 Windows
主服务后，打开**实际端口上的 `/steamvr`**；不需要另开 WebUI 端口。

### 优先使用 Windows 预编译包

只有与你当前代码版本对应的 **SteamVR Windows native build** 工作流成功后，
才可从 [GitHub Actions](https://github.com/miko8422/MikoType/actions/workflows/steamvr-windows.yml)
的该次运行下载 `mikotype-steamvr-windows-x64` artifact。没有对应的成功产物时，
请等待构建完成或使用下方源码构建方式；不能把失败的运行当作可用安装包。
使用预编译包不需要在本机安装 Visual Studio、CMake 或 OpenVR SDK，Python
主服务仍按前文使用 uv 或 Conda 安装。

1. 完成摄像头选择和键盘校准；应用键盘包后重启主服务。
2. 将预编译 artifact 解压到准备长期保留的目录，确认其下同时有 `dist/`、
   `scripts/` 和 `README.md`。它附带的是示例几何，不能直接替代你的校准键盘。
3. 在当前主服务的 `/steamvr` 下载**当前键盘资源**和 **Bridge 凭证**。
   将资源 ZIP 解压到一个空目录；凭证仅用于本机 bridge 鉴权，不要提交或分享，
   主服务重启后要重新下载。
4. **先关闭 SteamVR**，再在预编译包解压目录内打开 PowerShell，更新键盘资源并
   注册驱动。将下面的资源目录和 SteamVR 安装路径替换为本机实际路径：

```powershell
.\scripts\update-assets.ps1 -AssetDirectory "C:\MikoType\my-openvr-assets"
.\scripts\install.ps1 -SteamVrRoot "C:\Program Files (x86)\Steam\steamapps\common\SteamVR"
```

驱动注册指向这个目录中的文件，并不会把它们搬到另一个安装位置；注册后不要
删除或移动整个解压目录。卸载脚本只移除对应的驱动注册，保留文件。

5. 按平常的 Pimax 流程打开 SteamVR/Home；如有提示，在 Manage Add-ons 中启用
   `mikotypekeyboard`。在同一 PowerShell 目录启动 bridge，粘贴主服务打印的
   **完整准确地址**，不要猜测 8765 或固定使用 9000；凭证路径也替换为实际文件：

```powershell
$mikoServiceUrl = Read-Host "粘贴 OPEN THIS EXACT URL 后的完整地址"
.\scripts\run.ps1 -ServiceUrl $mikoServiceUrl -TokenFile "C:\MikoType\mikotype-steamvr-token.txt"
```

6. 在 WebUI 设置键盘的位置、旋转，然后明确确认位置并启用显示。位置单位是
   SteamVR standing 坐标系下的米；默认 pitch `-90°` 将键盘平放。需要戴头显
   手动对齐。这些参数只作用于本次运行，重启默认关闭并要求重新确认。
7. 观察连接、Home 进程、帧新鲜度和模型版本；写下头显里的实际现象，点击
   **收集 SteamVR 日志**，然后**导出诊断**。JSON 包含有上限的 bridge 日志和
   已收集的 SteamVR 日志末尾，不包含摄像头图片或凭证。分享前请检查隐私信息。

这些脚本不会替你终止 Pimax 或修改 VPN、代理设置；bridge 终端中按 Ctrl+C
只会停止 bridge。以后布局或模型变化时，重新导出当前资源，关闭 SteamVR 后
运行 `update-assets.ps1`，再重启 SteamVR 和 bridge 即可，无需重新编译。
服务与驱动模型的 SHA-256 不匹配时会主动隐藏输出，避免显示错误键盘。

### 备选：本机源码构建

没有匹配的成功预编译产物，或需要修改原生代码时，请按
[`integrations/steamvr/README.md`](integrations/steamvr/README.md) 的
**Build locally instead** 章节操作。此方式需要 Windows x64、Visual Studio 2022
Desktop development with C++、CMake 和
[OpenVR SDK v2.15.6](https://github.com/ValveSoftware/openvr/releases/tag/v2.15.6)。
uv / Conda 只管理 Python 环境，不能替代这些 C++ 构建工具。

### 显示与验收边界

这里通过 GenericTracker 提交真正的 3D 键盘模型，同时用**二维表面 Overlay**
叠加实时指尖 Bubble 和键位高亮。细键位轮廓也可用于 Home 不绘制 GenericTracker
时的平面回退，但不能把这个回退称为 3D 模型显示成功。驱动注册/API 成功不代表
Home 内一定可见，需要头显验收。

尚未实现指尖真实深度和自动摄像头→VR 米制 6DoF 配准；移动实体键盘或重设房间后
需重新手动对齐。摄像头/Marker 数据过期会隐藏 Overlay；bridge/主服务断开、
取消显示/确认、驱动模型不匹配会同时清除设备位姿。布局变化后需重新导出/安装
模型并重启 SteamVR。此处是待 Windows/头显验收的接入实现，不是 Mac 已验证
SteamVR 成功。生产 Python 不导入 Demo，也不要求安装 OpenVR SDK。

## 实验性分布式推理边界

未来可以实验在 Windows 保留摄像头和 SteamVR，将模型推理放到 macOS 或远端
服务器：

```text
Windows 摄像头 -> 经鉴权的 WSS JPEG 帧 -> 远端/macOS 推理
Windows SteamVR <- 匹配的 SceneState 结果 <- 远端/macOS 推理
```

这条链路在 **V0.1 中不会启用**。仓库目前只保留带版本、默认禁用的接口契约：

- [`contracts/remote_inference.schema.json`](contracts/remote_inference.schema.json)
  定义 JSON Envelope。
- 预留路由与子协议为 `/ws/experimental/inference` 和
  `mikotype.remote-inference.v0.1`；V0.1 不会注册它们。
- `RemoteFrameHeader` 后必须紧跟一个有大小上限的二进制 JPEG。
- `RemoteSceneStateEnvelope` 必须匹配相同的 Session、Sequence、Source 和 Frame ID。
- 网络中最多只能有一个尚未返回的最新帧。
- `configs/windows.yaml` 固定 `remote_inference.enabled: false`，V0.1 运行时会拒绝
  启用它。

后续网络适配器仍需实现带鉴权的 TLS、用户明确授权摄像头传输、断线与 TTL、
跨机时钟处理、带宽限制、远端推理服务和 Windows 返回状态客户端。现有调试
WebSocket 是无鉴权的本机诊断接口，不能直接暴露为远程视频服务。

## 本机 API 边界

本机浏览器和后续的同机 Windows VR 消费端可以使用：

- `GET /api/state` 或当前诊断用的 `WS /ws/state` 获取最新 `SceneState 0.2`。
  后续 VR 消费端必须根据 `emitted_at_ns` 自行执行 TTL，并在断线、状态过期、
  Revision 或 Schema 不匹配时清空；该接口目前不会主动发送 stale 事件。
- `GET /api/layout` 获取当前实体键位清单。
- `GET /api/model/manifest` 和 `GET /api/model/keyboard.glb` 获取自适应 3D 键盘。
- `GET /api/health` 获取本机诊断状态。
- `GET /api/camera` 获取摄像头状态；主动调用 `POST /api/camera/scan` 和
  `POST /api/camera/select` 扫描、切换设备，仅供本机摄像头面板使用，不是 VR 数据路径。
- `WS /ws/bundle`、`/snapshot.jpg` 和 `/stream.mjpg` 仅供本机检查界面使用，
  不进入 SteamVR 热路径。
- `/api/steamvr/status`、`/api/steamvr/diagnostics`、手动
  `POST /api/steamvr/collect-logs` 提供 VR 观测；bridge 使用凭证保护的
  `/api/steamvr/frame.bin` 和 `/api/steamvr/events`，完整字段见
  [接入契约](contracts/steamvr_bridge.md)。

## 项目结构

```text
src/deskvision/     共用的生产视觉与映射运行时
run_mikotype.py     固定加载当前仓库的源码入口
configs/            分离的 macOS 和 Windows 配置
data/keyboards/     校准示例和自适应 GLB
data/local/macos/   忽略提交的 Mac 校准、模型与 staging 文件
contracts/          SceneState 和实验性远端传输契约
tests/              隔离的自动化测试
demo/               实验代码，生产环境不会导入
dispose/            可恢复的退役内容，不进入运行时
windows_vr/         同机 Windows VR 集成边界
integrations/steamvr/ 独立编译的生产 Windows 驱动与实时 bridge
```

此前讨论过的实验功能继续隔离在 `demo/`。`dispose/` 存放退役框架和过期副本，
用于复查或恢复，不作为另一套实现或生产导入路径；保留内容和原位置见其 README。

## 测试

默认测试不会打开摄像头或启动 SteamVR：

```powershell
uv run --locked --extra test pytest -q
```

显式 Windows 摄像头测试：

```powershell
uv run --locked --extra test pytest -q -m "hardware and not soak"
uv run --locked --extra test pytest -q -m soak
```

macOS 使用 `configs/macos.yaml` 可以运行完整的非 VR 服务，Windows 使用
`configs/windows.yaml`。自动化测试通过不代表摄像头精度、请求帧率或 VR 硬件
验收通过；真实摄像头和人工测试需明确执行，与默认无硬件测试保持分离。

## 隐私与当前限制

- V0.1 的 FastAPI 只绑定回环地址，画面留在本机。
- Setup 永远只允许本机访问，而且没有上传图片入口。
- 远端推理默认关闭，目前没有可运行的网络适配器。
- Setup 产物通过版本匹配后才能启用。
- 未在配置中持久确认时，每次启动都需要明确确认 MediaPipe 指标说明。
- Windows 摄像头、退出行为、SteamVR Home 可见性、动态高亮和 6DoF 对齐仍需
  Windows + HMD 实机验收。

本仓库目前尚未选择开源许可证。
