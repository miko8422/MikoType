# MikoType

[English](README.md) | [简体中文](README.zh-CN.md)

MikoType 是一个面向 VR 的实体键盘视觉定位与交互管线。系统使用 MediaPipe
追踪手部，通过少量 ArUco Marker 建立键盘参考平面，将指尖映射到用户校准后
的键位，生成自适应 3D 键盘，并向 VR 消费端发布经过版本校验的场景数据。

> **V0.1 部署范围：**摄像头采集、MediaPipe/ArUco 推理、键盘映射、FastAPI
> 状态与模型服务以及 SteamVR 消费端都计划运行在同一台 Windows x64 电脑上。
> 这是 V0.1 唯一的生产拓扑。视觉与映射核心已经实现，但 Windows 实机验收和
> SteamVR 实时消费端仍未完成。

Python 分发包与旧命令为了兼容仍使用 `vr-desk-vision` 和 `deskvision`；新的
说明统一使用 `mikotype` 命令。

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

- Windows 10/11 x64 和 Python 3.12 x64。
- OpenCV 可以访问的摄像头。
- 在 Windows 隐私设置中允许桌面应用访问摄像头。
- 使用仓库附带的键盘包，或通过 Setup 流程生成新键盘包。

在 PowerShell 中执行：

```powershell
git clone https://github.com/miko8422/MikoType.git
cd MikoType

py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[test]"

.\.venv\Scripts\mikotype.exe check --config configs\windows.yaml
.\.venv\Scripts\mikotype.exe run `
  --config configs\windows.yaml `
  --acknowledge-mediapipe-metrics
```

打开 <http://127.0.0.1:8765/>。这会启动现有的摄像头、推理、映射、模型和本机
状态服务；目前还不会启动 SteamVR 实时消费端。

默认摄像头后端是 `msmf`。如果摄像头无法稳定打开，将
[`configs/windows.yaml`](configs/windows.yaml) 中的 `camera.backend` 依次
改为 `dshow`、`any`；OpenCV 选错摄像头时修改 `camera.device_index`。V0.1 会
请求分辨率和 FPS，但暂不验证所有摄像头驱动是否真正接受了这些参数。

## Windows 键盘校准

```powershell
.\.venv\Scripts\mikotype.exe setup `
  --config configs\windows.yaml `
  --acknowledge-mediapipe-metrics
```

打开 <http://127.0.0.1:8765/setup>。Setup 会定义用户拥有的键位、注册 Marker
Anchor、为每个键采集五次右手食指触点、校验全部产物版本并重建自适应 GLB。
应用完整键盘包后需要重启运行时。

## 同一台 Windows 上的 SteamVR 源码烟测

当前 SteamVR 工作仍是隔离的可行性 Demo。在同一台 Windows 电脑上启动资产
工具：

```powershell
$env:PYTHONPATH = "$PWD\src;$PWD"
.\.venv\Scripts\python.exe -m demo.steamvr_home_hybrid.app `
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
.\.venv\Scripts\python.exe -m pytest -q
```

显式 Windows 摄像头测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q -m "hardware and not soak"
.\.venv\Scripts\python.exe -m pytest -q -m soak
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
