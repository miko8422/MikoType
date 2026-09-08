# MikoType

[English](README.md) | [简体中文](README.zh-CN.md)

MikoType 是一个面向 VR 的实体键盘视觉定位与交互管线。系统使用 MediaPipe
追踪手部，通过少量 ArUco Marker 建立键盘参考平面，将指尖映射到用户校准后
的键位，生成自适应 3D 键盘，并向 VR 消费端发布经过版本校验的场景数据。

> **V0.1 部署范围：**摄像头采集、MediaPipe/ArUco 推理、键盘映射、FastAPI
> 状态与模型服务以及 SteamVR 消费端都计划运行在同一台 Windows x64 电脑上。
> 这是 V0.1 唯一的生产拓扑。视觉与映射核心已经实现，但 Windows 实机验收和
> SteamVR 实时消费端仍未完成。

Python 分发包与旧命令为了兼容仍使用 `vr-desk-vision` 和 `deskvision`。uv
说明使用 `mikotype` 命令；Conda 说明使用等价模块入口，从而不依赖在 `PATH`
中找到 `mikotype` 命令脚本。

## 已实现功能

- OpenCV 只保留最新帧的视频采集，支持 Windows MSMF、DSHOW 和自动选择后端。
- MediaPipe 双手 21 点追踪。
- 使用 `DICT_4X4_50` 稀疏 Marker 建立键盘参考坐标系。
- 用户键位清单、每键五次触点校准和完整版本一致性检查。
- 指尖 Bubble、可能直接接触的键位和按距离渐弱的周围键高亮。
- 每个键对应独立 `key:<physical_key_id>` 节点的自适应 GLB。
- 本机 FastAPI 检查界面、模型接口和 `SceneState 0.2` WebSocket。
- 仅包含源码的 Windows OpenVR Driver、Render Model 和 Overlay 烟测包。

系统输出的是“可能接触的键位”，不会将高亮声明为机械按压，也不会注入操作
系统键盘输入。

## V0.1 架构

```text
同一台 Windows x64 电脑

摄像头
  -> 最新 FramePacket
  -> MediaPipe 手部 + ArUco 键盘位姿
  -> 校准后的候选键与高亮
  -> 本机 SceneState + 自适应键盘 GLB
  -> 127.0.0.1 FastAPI
  -> 同机 SteamVR 消费端（下一个模块，尚未完成）
  -> SteamVR Home / 头显
```

所有实时视觉阶段使用同一个采集帧。下游变慢时会跳过已被替代的帧，不会堆积
延迟队列。V0.1 的本地服务被限制为回环地址，因此画面和状态不会离开 Windows
主机。

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

uv run --locked mikotype check --config configs\windows.yaml
uv run --locked mikotype run `
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

python -m deskvision.main check --config configs\windows.yaml
python -m deskvision.main run `
  --config configs\windows.yaml `
  --acknowledge-mediapipe-metrics
```

Conda 方案使用模块入口，不依赖在 `PATH` 中找到生成的 `mikotype` 命令脚本。
如果这个检出目录在加入该脚本前就做过 editable 安装，请先从仓库根目录诊断
当前解释器、加载的版本和导入路径：

```powershell
git pull --ff-only
conda activate mikotype
python -c "import sys; print(sys.executable)"
python -c "import deskvision; print(deskvision.__file__)"
python -m pip --version
python -m pip show vr-desk-vision
python -m deskvision.main --version
python -m deskvision.main doctor --config configs\windows.yaml
Get-Command mikotype -All -ErrorAction SilentlyContinue
```

本次版本中，`--version` 必须显示 `MikoType 0.1.0.dev1`，`doctor` 必须显示
`"status": "ready"`。`sys.executable` 应指向 `mikotype` Conda 环境；如果不是，
不要用这个 Python 重装软件包，而应重新打开 Conda PowerShell Prompt（或已由
Conda 初始化的 PowerShell），执行 `conda activate mikotype` 后再诊断。只有解释器
正确，但 `deskvision.__file__`、版本或 `doctor` 仍指向旧检出目录时，才修复项目的
editable 链接并再次检查：

```powershell
python -m pip install --force-reinstall --no-deps -e .
```

不要混用 uv 和 Conda 环境。后续命令以 uv 写法为准；如果已经激活 Conda
环境，不要再调用 uv：把 `uv run --locked mikotype <子命令>` 换成
`python -m deskvision.main <子命令>`；对于 uv 前缀后本来就是 `python -m ...` 的
命令，只保留 `python -m ...`；测试命令则把
`uv run --locked --extra test pytest -q` 换成 `python -m pytest -q`。Conda 同样
提供环境隔离，但只有推荐的 uv 方案会使用仓库中精确的跨平台依赖锁。

运行时就绪后，终端会打印 `OPEN THIS EXACT URL: ...`。只打开该地址，不要假定
最终使用的是 8765 端口。随后一个进程和一路摄像头会在同一本地控制台提供：

- `/`：严格同帧的视频、手部/键盘状态、质量指标和自适应键盘高亮。
- `/settings`：校验并保存白名单内的摄像头、预览、MediaPipe、Marker、交互、
  流水线和诊断参数。
- `/setup`：调整现有键位的位置与尺寸、注册 Marker Anchor、按键位图
  顺序采集指尖触点，并重建自适应 3D 键盘。

WebUI 参数会原子写入 Git 忽略的 `configs/windows.local.yaml`，下次启动时自动
加载。运行中的摄像头和推理对象不会被局部热替换，界面会明确提示需要重启。

配置中的端口现在是首选端口，而不是必须占用的固定端口。`run` 和 `setup` 默认从
首选端口起连续扫描最多 20 个回环端口；使用默认配置时范围为 8765 至 8784。如果
其中已有“相同配置 + 同一 Setup 工作区”的 MikoType，CLI 会复用它；否则会在打开
摄像头前预留第一个空闲端口。如果范围内既没有匹配实例也没有空闲端口，启动才会
失败，且不会打开摄像头。

MikoType 不会终止或重新配置占用端口的进程。Pimax 软件及其他任何本机服务都会
保持运行，扫描只会继续尝试下一个端口。身份探测只直接访问回环地址，并仅对这些
请求绕过继承的 HTTP(S) 代理；它不会修改 Windows 代理设置、VPN 状态、路由或
其他应用的网络配置。

始终只打开终端在 `OPEN THIS EXACT URL:` 后打印的地址。如果必须固定使用首选
端口，可显式启用严格模式：

```powershell
uv run --locked mikotype run `
  --config configs\windows.yaml `
  --strict-port `
  --acknowledge-mediapipe-metrics
```

严格模式下，如果首选端口属于无关服务或身份不匹配的进程，MikoType 会在打开
摄像头前失败。目前仍不会启动 SteamVR 实时消费端。

默认摄像头后端是 `msmf`。如果摄像头无法稳定打开，可在参数设置页依次尝试
`dshow`、`any`；OpenCV 选错摄像头时修改设备编号。V0.1 会请求分辨率和 FPS，
但暂不验证所有摄像头驱动是否真正接受了这些参数。

## Windows 键盘校准

主 `run` 命令运行时，从终端打印的准确地址进入控制台并打开其中的 Setup 页面；
不要假定端口为 8765，也不要再启动第二个服务。下面的兼容命令只在没有匹配的
MikoType 实例时启动同一个集成控制台；若已经运行，它会打印现有 Setup 的准确
地址：

```powershell
uv run --locked mikotype setup `
  --config configs\windows.yaml `
  --acknowledge-mediapipe-metrics
```

Setup 会使用现有布局的键位清单和校准顺序，允许用户修正每个键的位置与尺寸、
注册 Marker Anchor、为每个键采集五次右手食指触点、校验全部产物版本，并重建
自适应 GLB。应用完整键盘包后需要重启运行时。V0.1 中新增/删除 key ID、
修改标签或 Marker Anchor 分配仍需手动编辑 Layout 文件。

## 同一台 Windows 上的 SteamVR 源码烟测

当前 SteamVR 工作仍是隔离的可行性 Demo。在同一台 Windows 电脑上启动资产
工具：

```powershell
$env:PYTHONPATH = "$PWD\src;$PWD"
uv run --locked python -m demo.steamvr_home_hybrid.app `
  --host 127.0.0.1 --port 8776
```

打开 <http://127.0.0.1:8776/>，点击“验证并生成源码包”。准备好 SteamVR、
头显、Visual Studio 2022 Desktop C++、CMake 和
[OpenVR SDK 2.15.6](https://github.com/ValveSoftware/openvr/releases/tag/v2.15.6)
后，继续在 PowerShell 中执行：

```powershell
$SteamVrSmokeDir = Join-Path $PWD ("steamvr-smoke-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
Expand-Archive `
  .\demo\steamvr_home_hybrid\output\deskvision_steamvr_home_windows_source.zip `
  -DestinationPath $SteamVrSmokeDir
Set-Location (Join-Path $SteamVrSmokeDir "deskvision_steamvr_home_smoke_source")

.\packaging\build.ps1 -OpenVrSdkRoot "C:\path\to\openvr"
.\packaging\install.ps1
```

随后手动重启 SteamVR，在 **Manage Add-ons** 中启用 `deskvisionkeyboard`，等待
`vrserver` 正常运行后，再单独执行：

```powershell
.\packaging\smoke.ps1
```

完成日志和头显检查后再清理：

```powershell
.\packaging\uninstall.ps1
```

当前烟测使用固定头显相对位置的 `GenericTracker` 和静态 Overlay。它尚未消费
实时 FastAPI 状态、渲染动态键帽高亮，也没有摄像机到 SteamVR 的米制 6DoF
对齐。这些仍是下一个 Windows + HMD 开发和验收任务。

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

同机 Windows 消费端可以使用：

- `GET /api/state` 或当前诊断用的 `WS /ws/state` 获取最新 `SceneState 0.2`。
  后续 VR 消费端必须根据 `emitted_at_ns` 自行执行 TTL，并在断线、状态过期、
  Revision 或 Schema 不匹配时清空；该接口目前不会主动发送 stale 事件。
- `GET /api/layout` 获取当前实体键位清单。
- `GET /api/model/manifest` 和 `GET /api/model/keyboard.glb` 获取自适应 3D 键盘。
- `GET /api/health` 获取本机诊断状态。
- `WS /ws/bundle`、`/snapshot.jpg` 和 `/stream.mjpg` 仅供本机检查界面使用，
  不进入 SteamVR 热路径。

## 项目结构

```text
src/deskvision/     面向 Windows 的生产视觉与映射运行时
configs/            Windows V0.1 标准配置
data/keyboards/     校准示例和自适应 GLB
contracts/          SceneState 和实验性远端传输契约
tests/              隔离的自动化测试
demo/               实验代码，生产环境不会导入
windows_vr/         同机 Windows VR 集成边界
```

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

非 Windows 主机可以执行离线检查和自动化测试，但生产 `run` 与 `setup` 命令会
在打开硬件前拒绝启动。

## 隐私与当前限制

- V0.1 的 FastAPI 只绑定回环地址，画面留在同一台 Windows 主机。
- Setup 永远只允许本机访问，而且没有上传图片入口。
- 远端推理默认关闭，目前没有可运行的网络适配器。
- Setup 产物通过版本匹配后才能启用。
- 未在配置中持久确认时，每次启动都需要明确确认 MediaPipe 指标说明。
- Windows 摄像头、退出行为、SteamVR Home 可见性、动态高亮和 6DoF 对齐仍需
  Windows + HMD 实机验收。

本仓库目前尚未选择开源许可证。
