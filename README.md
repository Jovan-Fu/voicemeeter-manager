# 立体音管理工具 2.1.4

Windows 上的 VoiceMeeter Banana 配置、隐藏自启动、默认声音设备和 Chrome 音频设置工具。

2.1.4 在已确认工作的 2.1.3 上修复安全审核问题。内置预设保持原文件不变：第四列“通道1”为 A1+B1，第五列“通道2”为 A1+B2；保留完整自动结束、隐藏重开和载入核验流程。自定义导入 XML 仍按文件内容载入。

安全修复与待验证事项见仓库内 `SECURITY-2.1.4.html` 表格。XML 属性改为线性扫描；Chrome 写入增加内容冲突检测及提交瞬间原文件保留；启用 VBAN 的外部配置在原有确认框中展示音频、控制和 MIDI 网络用途，并绑定被确认文件的 SHA-256。确认后可正常使用 VBAN。

安装流程等待安全验证进程和官方向导结束后报告结果、清理本次下载缓存；超过 15 分钟仍无法确认结束时保留缓存，不结束安装器。历史缓存不自动删除。Chrome 页面增加手动清理旧备份按钮，删除前确认，只清理超过 30 天且不在最近 10 份中的普通备份，保留冲突恢复文件。

此仓库仅提交源码；不含 EXE、DLL、已下载驱动或打包缓存。声音设备检测和设置直接调用 Windows Core Audio，不再依赖 `AudioHelper.exe` 或 .NET 音频助手。管理程序由 GitHub Actions 自动生成。

## 上传 GitHub 自动生成 EXE

1. 将本目录里的**全部内容**上传到仓库根目录，包括 `.github`、`tools` 和 `tests`。仓库根目录应直接看到 `vm_gui.py`，不要额外套一层目录。
2. 提交后打开仓库 **Actions → Build Windows EXE**。也可从该页面点 **Run workflow** 手动触发。
3. 流程测试通过后打包 `StereoAudioManager.exe`，无需编译或分发额外音频助手。
4. 在成功任务底部 **Artifacts** 下载 `StereoAudioManager-Windows-x64`，解压运行。包内还有 `build-info.json` 和 `SHA256SUMS.txt`，记录源码摘要、Python/PyInstaller 版本、提交号和 EXE SHA-256。

当前工作流生成 Actions 构建产物，不自动创建公开 Release，也不上传到其他服务。产物保留 30 天。新建仓库若禁用了 Actions，需先启用。Actions 使用固定提交摘要；Dependabot 按月提出更新。

构建机制参考 [GitHub Python 工作流文档](https://docs.github.com/en/actions/tutorials/build-and-test-code/python) 和 [upload-artifact 官方说明](https://github.com/actions/upload-artifact)。

## 自启动如何工作

检测的是 **VoiceMeeter 自身**，不是本工具。会解析用户/所有用户 Startup 中快捷方式的实际目标，以及 HKCU/HKLM、32/64 位 Run 项，并读取 Windows 对启动项的禁用状态。

- 已启用且带 `-h`：显示“已设置隐藏自启动”，设置按钮禁用；后端重复调用也不写入。
- 已启用但未隐藏：保留原目标及配置参数，只对当前用户现有项补 `-h`。
- 没有启动项：创建 VoiceMeeter 原生命名 `Voicemeeter (VB-Audio).LNK`，目标为已安装的 Banana，参数 `-h`。由 VoiceMeeter 加载自己保存的配置，不引用打包 `_MEI` 临时文件。
- 已有项被 Windows 禁用：明确提示在 Windows 设置 → 应用 → 启动中启用，不另建重复项绕过禁用。
- 无法完整读取：显示“无法检测”，不会错误提示未设置，也不会新增启动项。
- 所有用户启动项需要修改时：提示手动在厂商软件/Windows 中调整，不为自启动请求管理员权限。

如果发现多个有效启动项会提示数量，但不会擅自删除用户自定义项。自启动只启动 VoiceMeeter，本工具不会开机运行。快捷方式通过 Windows Shell Link COM 读取/保存，不再为自启动拉起 PowerShell。

`-h` 用于隐藏 VoiceMeeter 主窗口，并非关闭合法的许可证/捐赠提示。旧版写入试用计数的逻辑已移除。

## 一键载入与自动重新打开

点击蓝色“一键应用并载入配置到 VoiceMeeter”后，完整执行：固定选中 XML → 请求正常退出 → 超时后自动结束当前会话、安装路径下的 Banana 进程 → 保存配置并设置默认菜单项 → 隐藏重新打开 → 用厂商 `Command.Load` 明确载入 → 读回核对核心参数。过程中无需手动到任务管理器结束进程或重新打开软件。

载入检查覆盖选中 XML 中的静音、独奏、单声道、增益及 A1/A2/A3/B1/B2 路由；内置预设对应 60 项。数值不一致时继续等待/重发载入请求，超时明确报告差异，不能仅因进程打开就显示“配置成功”。这项检查不等于逐项证明硬件设备可用、音质或全部 EQ 内容正确。

退出前先固定 XML，避免选中的 LastSettings 在 VoiceMeeter 退出时被当前乱掉的状态覆盖。保留原来的默认菜单设置步骤；独立菜单按钮在软件运行时也复用自动退出/载入/重新打开流程。后台操作会显示进度，并在完成前禁用重复点击载入。

自动结束只针对当前 Windows 会话、安装路径下的 `voicemeeterpro.exe` / `voicemeeterpro_x64.exe`，不会用名称通配符结束其他音频软件或其子进程。权限不足等真实失败会明确提示，不覆盖配置后谎报成功。强制结束会丢失未保存的临时调整，执行前界面会说明音频中断和覆盖行为。

Remote API 采用厂商要求的单次登录、进程退出时注销，避免异步载入/退出命令还未处理就注销。参考：[VoiceMeeter 官方 Remote API 文档](https://download.vb-audio.com/Download_CABLE/VoicemeeterRemoteAPI.pdf)。

## 其他已修复的逻辑问题

- 漏识别 VoiceMeeter 自己创建的启动项，导致误报未设置及重复操作。
- XML 导入区分只保存和重启应用；需要重启时自动执行上述完整载入链路。默认配置保存到稳定的 Documents 路径，两个默认文件先备份、原子替换，后一个失败时尝试回滚前一个。
- 内置样例移除其他电脑的固定录音路径，应用时填入当前用户的配置目录；不会改写用户自行导入文件中的自定义路径。
- 默认菜单设置在自动退出后保存，避免被退出时的旧值覆盖，再自动重新打开，不要求先手动退出。
- Chrome 配置要求浏览器退出，并在提交前检查原文件哈希。Windows ReplaceFileW 捕获实际被替换文件；发现提交瞬间竞争写入时保留冲突副本、尝试恢复当刻原文件并报告失败。持续外部写入无法作为跨进程事务完全隔离，捕获的恢复副本会保留，不会静默报成功。
- Chrome 多开快捷方式改用原生 COM，验证 Profile 标识，不再从账号名构造脚本；同名账号不会互相覆盖，已有同名快捷方式保留并报告跳过。
- 多个配置写入操作互斥；失败会传回界面，不能继续显示全部成功。
- 状态查询异常显示“无法检测”，与“未安装/未设置”分开；刷新不再自动编译并执行缺失的辅助程序。
- 声音设备检测直接读取 Windows Core Audio，不再启动外部音频助手。播放/录制错误分别展示；显示 Windows HRESULT，可复制检测详情。某一端或次要设备异常不再遮住另一端有效结果。
- 设置默认设备前检查全部选中目标；设备缺失或同名歧义时不修改任何设备。设置普通、多媒体、通信默认角色后逐项复核，失败时尝试恢复原设备并说明可能残留的变化。

高风险驱动安装保护仍保留：固定官网包 SHA-256、预期厂商签名、管理员受保护目录中的二次复核。厂商换包或换证会停止安装，需要维护审核后更新。没有关闭安全软件或添加排除项的功能。

## 音频与浏览器设置

“找回旧版音频设置”对应截图中的 `Temporarily unexpire M154 / M155 flags`；“关闭麦克风自动调音量”对应 `Allow WebRTC to adjust the input volume`。各版本提供的选项不同，已保存的字符串不代表该版本仍支持或正在运行的窗口已生效。

Chrome 独立用户数据目录分别保存设置。最近使用账号来自 Chrome 记录，不能保证代表最后一个无痕窗口。地址栏导航仅对确认身份的 Chrome 前台窗口发送固定网址，焦点变化时停止。

立体音路由、网站麦克风权限、输入输出设备仍需正确配置；这些 flags 不能保证解决全部 GPT 翻译问题。

厂商来源：[VB-CABLE](https://vb-audio.com/Cable/)、[VAC Lite](https://vac.muzychenko.net/en/download.htm)、[VoiceMeeter Banana](https://vb-audio.com/Voicemeeter/banana.htm)。

## 源码运行与检查

运行时仅用 Python 标准库（含 Windows Tkinter），建议 Python 3.14.10 或更新的受支持修复版本。直接运行 `python -B vm_gui.py` 即可检测声音设备，无需 `AudioHelper.exe`、.NET 或额外 pip 音频包。GitHub 构建固定 Python 3.14.10，构建入口拒绝较低版本；7 个完整构建依赖固定版本和 Windows x64 wheel SHA-256，禁止源码包回退。系统共享 Python 未被本次源码修复升级。

```powershell
python -B -m unittest discover -s tests -v
python -B tools/build.py --check-only
```

如需本地自行构建，先 `python -m pip install -r requirements-build.txt`，再运行 `python tools/build.py`。构建是显式动作，不会由界面状态刷新触发。

## 验证与限制

本地 86 项回归测试通过，包括原有 67 项，以及大属性解析、真实临时文件 ReplaceFileW 竞争写入/恢复、VBAN 确认与文件变化检测、安装完成/失败/超时模拟、缓存保留与清理、旧备份清理和 Chrome 窗口/修饰键保护。完整构建依赖通过 pip dry-run 解析及哈希核验，未安装依赖或生成 EXE。

此前版本已在真实 VoiceMeeter 上只读核对内置 XML 的 60 项核心参数；此前 Core Audio 检测也已确认默认播放为 Voicemeeter Input、默认录制为 Voicemeeter Out B1。本次验证没有在当前音频会话执行真实强制结束/载入，没有改动本机自启动、Chrome 配置或音频路由，也没有实际执行驱动安装；完整重启与载入测试使用模拟进程/引擎，不宣称等同于实机听音验收。

Chrome 自动导航增加浏览器窗口类型和修饰键检查；SendInput 仍是全局输入，不能彻底排除检查后焦点变化的短时竞争。如果工具停止自动输入，可直接复制界面给出的固定 chrome://flags 地址自行打开。

读取设备使用微软公开的 MMDevice/IPropertyStore 接口。设置默认设备沿用 Windows IPolicyConfig 接口，只在明确点击设置时创建；它不是微软公开承诺稳定的接口，未来系统不支持时会明确报错。参考：[MMDeviceEnumerator](https://learn.microsoft.com/en-us/windows/win32/api/mmdeviceapi/nn-mmdeviceapi-immdeviceenumerator)。

未代用户上传仓库，因此尚未取得真实 GitHub Actions 成功记录。请以上传后工作流结果为准。此源码包不包含预生成 EXE。

GitHub 自动构建并不等于数字签名或杀毒认证，不能保证消除杀毒告警。截图只能确认安全软件在消毒，无法确定具体检出对象和分类；发生告警时保留检出名称/路径/哈希供核查，不要关闭防护。
