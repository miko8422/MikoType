# MikoType

[English](README.md) | [简体中文](README.zh-CN.md)

MikoType 是一个面向 VR 实验的实体键盘视觉定位与交互管线。系统使用
MediaPipe 追踪手部，通过少量 ArUco Marker 建立稳定的键盘参考坐标系，
将指尖映射到用户校准后的键位，生成自适应 3D 键盘，并通过本地可视化与
最新版 SceneState 为后续 VR 消费端提供数据。

> **V0.1 状态：**生产视觉和键盘映射管线已在 macOS 完成实现与验证。
> Windows 摄像头运行以及 SteamVR/Home 消费端仍属于待实机验收目标，
> 不能视为已经完成的生产支持。

Python 分发包与命令行为了兼容仍使用 `vr-desk-vision` 和 `deskvision`；
MikoType 是项目与 GitHub 仓库名称。

## 已实现功能

- OpenCV 低延迟、只保留最新帧的视频采集与生命周期监控。
- MediaPipe 双手 21 点追踪。
- 使用 `DICT_4X4_50` 稀疏 ArUco 锚点建立键盘参考坐标系。
- 用户键位清单、每键五次触点校准和完整的版本一致性检查。
- 指尖 Bubble、直接命中键与周围键渐弱高亮。
- 每个键对应独立 `key:<physical_key_id>` 节点的自适应 GLB。
- 本地 FastAPI 检查界面和 WebSocket `SceneState 0.2` 数据流。
- 与生产隔离的 Windows OpenVR Driver/Render Model 可行性验证包。

系统输出的是“可能接触的键位”，不会将高亮声明为机械按压或操作系统键盘事件。

## 运行数据流

```text
摄像头
  -> FramePacket（进程内同一张 BGR 图像）
  -> MediaPipe 手部 + ArUco 键盘位姿
  -> 校准后的指尖候选键
  -> 直接/周围高亮状态
  -> 最新 SceneState + 本地 WebUI/WebSocket
  -> 后续 Windows SteamVR 消费端
```

所有实时阶段绑定同一个源帧。下游变慢时系统会丢弃被替代的数据，不会累积延迟队列。

## 项目结构

```text
src/deskvision/     生产运行时代码
configs/            macOS 与实验性 Windows 配置
data/keyboards/     校准示例和自适应 GLB
contracts/          对外 SceneState 契约
tests/              隔离的自动化测试
demo/               选定的实验代码，生产环境不会导入
windows_vr/         VR 生产集成边界
```

## 环境要求

- 推荐 Python 3.11 或 3.12。
- OpenCV 可以访问的摄像头。
- 使用仓库附带的键盘示例，或通过 Setup 流程生成新的校准产物。
- 启动手部追踪时，需要明确确认 MediaPipe Tasks 的性能/利用率指标说明。

## macOS 启动

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[test]'

.venv/bin/python -m deskvision.main check --config configs/dev.yaml
.venv/bin/python -m deskvision.main run \
  --config configs/dev.yaml \
  --acknowledge-mediapipe-metrics
```

为终端或宿主程序授予摄像头权限，然后打开 <http://127.0.0.1:8765/>。

需要调整键盘布局、注册锚点或重新采集触点时：

```bash
.venv/bin/python -m deskvision.main setup \
  --config configs/dev.yaml \
  --acknowledge-mediapipe-metrics
```

打开 <http://127.0.0.1:8765/setup>。应用新的完整键盘包后，需要重启运行时。

## Windows 启动（实验性）

Windows 配置使用 OpenCV 自动选择摄像头后端。目前这条路径用于下一阶段部署
测试，Windows 摄像头延迟、MediaPipe 性能、采集参数生效情况和进程退出仍需
在真实硬件上验收。

在 PowerShell 中执行：

```powershell
git clone https://github.com/miko8422/MikoType.git
cd MikoType

py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[test]"

.\.venv\Scripts\python.exe -m deskvision.main check --config configs\windows.yaml
.\.venv\Scripts\python.exe -m deskvision.main run `
  --config configs\windows.yaml `
  --acknowledge-mediapipe-metrics
```

在 Windows 隐私设置中允许桌面应用访问摄像头，然后打开
<http://127.0.0.1:8765/>。摄像头选择错误时，修改
[`configs/windows.yaml`](configs/windows.yaml) 中的 `camera.device_index`。

键盘 Setup 使用相同配置：

```powershell
.\.venv\Scripts\python.exe -m deskvision.main setup `
  --config configs\windows.yaml `
  --acknowledge-mediapipe-metrics
```

## SteamVR/Home 可行性路径

现代 macOS 已不受 SteamVR 支持，因此 OpenVR 验证与生产视觉进程保持隔离。

检查当前模型并生成 Windows 源码交接包：

```bash
PYTHONPATH=src:. .venv/bin/python \
  -m demo.steamvr_home_hybrid.app --host 127.0.0.1 --port 8776
```

打开 <http://127.0.0.1:8776/>，点击“验证并生成源码包”。在具备 SteamVR、
头显、Visual Studio 2022 Desktop C++、CMake 和固定版本 OpenVR SDK 的
Windows x64 机器上解压，然后运行：

```powershell
.\packaging\build.ps1 -OpenVrSdkRoot "C:\path\to\openvr"
.\packaging\install.ps1
# 重启 SteamVR，并启用 deskvisionkeyboard Add-on。
.\packaging\smoke.ps1
```

当前烟测只注册固定位置的 `GenericTracker`、加载自适应键盘 Render Model，
并测试与键盘宽高比一致的静态 Overlay。它尚未实现摄像机到 SteamVR 的真实
6DoF 标定，也未接入动态高亮。只有在 Windows 头显内确认 SteamVR Home
持续显示模型后，才会将动态桥接并入生产。测试结束后使用
`.\packaging\uninstall.ps1` 移除开发 Driver 注册。

## 测试

默认测试不会打开真实摄像头，也不会运行长时间稳定性测试：

```bash
.venv/bin/python -m pytest -q
```

显式硬件测试：

```bash
.venv/bin/python -m pytest -q -m "hardware and not soak"
.venv/bin/python -m pytest -q -m soak
```

## 隐私与安全边界

- 默认 Web 服务只绑定本机回环地址。
- 除非未来显式配置传输端，否则摄像头画面不会离开本地进程。
- Setup 产物通过版本匹配后才能启用。
- 未在配置中持久确认时，每次启动都需要显式确认 MediaPipe 指标说明。
- 架构测试保证 `src/deskvision` 不会导入实验或测试代码。

本仓库目前尚未选择开源许可证。
