# -*- coding: utf-8 -*-
"""
VoiceMeeter Banana 全能管理与配置工具 (GUI 界面)
功能包含：
1. 【科技蓝】VoiceMeeter Banana 核心配置导入、通道预览、开机自启管理 (绿色防拦截模式)
2. 【翡翠绿】系统声音默认设备检测与一键对齐截图预设 (Voicemeeter Input / Out B1)
3. 【活力橙】Chrome 浏览器 Flags 自动配置 (禁用 WebRTC 自动增益与全局回声消除)
4. 【典雅紫】依赖驱动与软件一键安装 (VB-CABLE 与 VAC Lite 官方向导修复版)
"""

import os
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# 启用 Windows 高 DPI 感知，防止界面模糊
try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

import vm_manager
from xml_config import inspect_file as inspect_config_file
from backup_maintenance import cleanup_plan, apply_cleanup

# 四大分类主题色定义
THEME_COLORS = {
    0: {"name": "VoiceMeeter 配置", "primary": "#1976D2", "dark": "#0D47A1", "light": "#E3F2FD"},
    1: {"name": "系统声音预设", "primary": "#2E7D32", "dark": "#1B5E20", "light": "#E8F5E9"},
    2: {"name": "谷歌 Chrome 优化", "primary": "#E65100", "dark": "#BF360C", "light": "#FFF3E0"},
    3: {"name": "依赖驱动安装", "primary": "#6A1B9A", "dark": "#4A148C", "light": "#F3E5F5"},
}


class VoiceMeeterManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("立体音管理工具 · 2.1.5")
        self.root.geometry("880x760")
        self.root.minsize(820, 680)

        # 变量定义
        self.current_xml_path = tk.StringVar()
        self.status_data = {}
        self.current_tab_index = 0

        # 初始寻找默认配置
        self.find_initial_config()

        # 配置现代样式
        self.setup_styles()
        # 构建 UI 布局
        self.create_widgets()
        # 刷新所有状态
        self.refresh_all_status()

    def setup_styles(self):
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except Exception:
            pass

        style.configure("Header.TLabel", font=("Microsoft YaHei", 11, "bold"))
        style.configure("SubHeader.TLabel", font=("Microsoft YaHei", 9, "bold"))
        style.configure("Normal.TLabel", font=("Microsoft YaHei", 9))
        style.configure("StatusSuccess.TLabel", font=("Microsoft YaHei", 9, "bold"), foreground="#2e7d32")
        style.configure("StatusWarn.TLabel", font=("Microsoft YaHei", 9, "bold"), foreground="#d32f2f")
        style.configure("StatusInfo.TLabel", font=("Microsoft YaHei", 9), foreground="#0277bd")
        style.configure("Accent.TButton", font=("Microsoft YaHei", 10, "bold"), padding=6)
        style.configure("Action.TButton", font=("Microsoft YaHei", 9), padding=4)

    def find_initial_config(self):
        """寻找初始可用的 XML 配置文件"""
        sample_path = vm_manager.get_resource_path("sample_config.xml")
        doc_last = os.path.join(vm_manager.DOCS_VM_DIR, "VoicemeeterBanana_LastSettings.xml")
        doc_today = os.path.join(vm_manager.DOCS_VM_DIR, "VoicemeeterBanana_TodaySettings.xml")

        if os.path.exists(sample_path):
            self.current_xml_path.set(sample_path)
        elif os.path.exists(doc_last):
            self.current_xml_path.set(doc_last)
        elif os.path.exists(doc_today):
            self.current_xml_path.set(doc_today)
        else:
            self.current_xml_path.set("")

    def create_widgets(self):
        # 1. 顶部全局服务状态卡片
        self.create_header_card()

        # 2. 鲜艳醒目的分类导航栏 (4 大高对比度色彩分类)
        self.create_category_nav_bar()

        # 3. 多页面容器
        self.page_container = ttk.Frame(self.root, padding=(12, 6, 12, 6))
        self.page_container.pack(fill=tk.BOTH, expand=True)

        self.pages = []
        self.page_contents = []
        self.page_canvases = []
        for i in range(4):
            page = ttk.Frame(self.page_container)
            self.pages.append(page)
            canvas = tk.Canvas(page, highlightthickness=0, bg=self.root.cget("bg"))
            scrollbar = ttk.Scrollbar(page, orient="vertical", command=canvas.yview)
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            canvas.configure(yscrollcommand=scrollbar.set)
            content = ttk.Frame(canvas)
            item = canvas.create_window((0, 0), window=content, anchor="nw")
            content.bind("<Configure>", lambda event, c=canvas: c.configure(scrollregion=c.bbox("all")))
            canvas.bind("<Configure>", lambda event, c=canvas, w=item: c.itemconfigure(w, width=event.width))
            self.page_contents.append(content)
            self.page_canvases.append(canvas)
        self.root.bind("<MouseWheel>", self.on_page_scroll)

        # 构建各页面内容
        self.setup_page_vm_config(self.page_contents[0])
        self.setup_page_audio(self.page_contents[1])
        self.setup_page_chrome(self.page_contents[2])
        self.setup_page_drivers(self.page_contents[3])

        # 默认显示第 0 页
        self.switch_to_tab(0)

        # 4. 底部操作日志
        self.create_footer_log()

    # --------------------------------------------------------------------------
    # 顶部全局状态卡片
    # --------------------------------------------------------------------------
    def create_header_card(self):
        frame = ttk.LabelFrame(self.root, text=" 核心服务与驱动状态汇总 ", padding=8)
        frame.pack(fill=tk.X, padx=12, pady=(8, 4))

        status_grid = ttk.Frame(frame)
        status_grid.pack(fill=tk.X)

        ttk.Label(status_grid, text="VoiceMeeter:", style="SubHeader.TLabel").grid(row=0, column=0, sticky="w", padx=4, pady=2)
        self.lbl_vm_status = ttk.Label(status_grid, text="检测中...", style="Normal.TLabel")
        self.lbl_vm_status.grid(row=0, column=1, sticky="w", padx=4, pady=2)

        ttk.Label(status_grid, text="VB-CABLE 驱动:", style="SubHeader.TLabel").grid(row=0, column=2, sticky="w", padx=(15, 4), pady=2)
        self.lbl_cable_status = ttk.Label(status_grid, text="检测中...", style="Normal.TLabel")
        self.lbl_cable_status.grid(row=0, column=3, sticky="w", padx=4, pady=2)

        ttk.Label(status_grid, text="VAC Lite 驱动:", style="SubHeader.TLabel").grid(row=0, column=4, sticky="w", padx=(15, 4), pady=2)
        self.lbl_vac_status = ttk.Label(status_grid, text="检测中...", style="Normal.TLabel")
        self.lbl_vac_status.grid(row=0, column=5, sticky="w", padx=4, pady=2)

        btn_bar = ttk.Frame(frame)
        btn_bar.pack(fill=tk.X, pady=(6, 0))

        ttk.Button(btn_bar, text="▶ 启动软件", style="Action.TButton", command=self.on_launch_app).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_bar, text="⏹ 关闭软件", style="Action.TButton", command=self.on_kill_app).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_bar, text="🔄 重启音频引擎", style="Action.TButton", command=self.on_restart_engine).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_bar, text="🔄 刷新状态", style="Action.TButton", command=self.refresh_all_status).pack(side=tk.RIGHT, padx=3)

    # --------------------------------------------------------------------------
    # 高对比度彩色分类导航栏
    # --------------------------------------------------------------------------
    def create_category_nav_bar(self):
        nav_box = tk.Frame(self.root, bg="#E0E0E0", height=48)
        nav_box.pack(fill=tk.X, padx=12, pady=(4, 6))

        self.nav_buttons = []
        titles = [
            ("🎛️  1. 立体音核心配置", 0),
            ("🔊  2. 系统声音默认预设", 1),
            ("🌐  3. 谷歌浏览器配置", 2),
            ("📦  4. 依赖驱动一键安装", 3),
        ]

        for text, idx in titles:
            theme = THEME_COLORS[idx]
            btn = tk.Button(
                nav_box,
                text=text,
                font=("Microsoft YaHei", 10, "bold"),
                relief=tk.FLAT,
                bd=0,
                padx=14,
                pady=8,
                cursor="hand2",
                command=lambda i=idx: self.switch_to_tab(i)
            )
            btn.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=2, pady=2)
            self.nav_buttons.append(btn)

    def switch_to_tab(self, index):
        """切换分类页面并更新按钮高亮色彩"""
        self.current_tab_index = index
        for i, page in enumerate(self.pages):
            if i == index:
                page.pack(fill=tk.BOTH, expand=True)
            else:
                page.pack_forget()

        # 更新导航按钮的高亮颜色
        for i, btn in enumerate(self.nav_buttons):
            theme = THEME_COLORS[i]
            if i == index:
                btn.config(
                    bg=theme["primary"],
                    fg="#FFFFFF",
                    activebackground=theme["dark"],
                    activeforeground="#FFFFFF"
                )
            else:
                btn.config(
                    bg="#FFFFFF",
                    fg="#424242",
                    activebackground=theme["light"],
                    activeforeground=theme["primary"]
                )

    def create_category_banner(self, parent, category_index, title_text, sub_desc):
        """创建醒目的彩色分类头部横幅"""
        theme = THEME_COLORS[category_index]
        banner = tk.Frame(parent, bg=theme["primary"], padx=12, pady=8)
        banner.pack(fill=tk.X, pady=(0, 8))

        lbl_title = tk.Label(
            banner,
            text=title_text,
            font=("Microsoft YaHei", 11, "bold"),
            bg=theme["primary"],
            fg="#FFFFFF", justify="left"
        )
        lbl_title.pack(anchor="w")
        banner.bind("<Configure>", lambda e: (lbl_title.config(wraplength=max(350, e.width - 24)), lbl_desc.config(wraplength=max(350, e.width - 24))))

        lbl_desc = tk.Label(
            banner,
            text=sub_desc,
            font=("Microsoft YaHei", 9),
            bg=theme["primary"],
            fg="#E0E0E0", justify="left"
        )
        lbl_desc.pack(anchor="w", pady=(2, 0))

    # --------------------------------------------------------------------------
    # 分类 1: 【科技蓝】VoiceMeeter 核心配置
    # --------------------------------------------------------------------------
    def setup_page_vm_config(self, parent):
        self.create_category_banner(
            parent, 0,
            "🎛️ 分类一：立体音核心配置",
            "设置立体音默认配置和默认设置（包括开机自启动、系统托盘等）"
        )

        # 1. 配置文件选择器
        sel_frame = ttk.LabelFrame(parent, text=" 配置文件路径 (XML) ", padding=8)
        sel_frame.pack(fill=tk.X, pady=(0, 6))

        path_row = ttk.Frame(sel_frame)
        path_row.pack(fill=tk.X)

        self.entry_path = ttk.Entry(path_row, textvariable=self.current_xml_path, font=("Segoe UI", 9))
        self.entry_path.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        ttk.Button(path_row, text="浏览文件...", style="Action.TButton", command=self.on_browse_xml).pack(side=tk.LEFT, padx=2)
        ttk.Button(path_row, text="使用内置 sample_config", style="Action.TButton", command=self.on_select_sample_config).pack(side=tk.LEFT, padx=2)
        ttk.Button(path_row, text="文档默认配置", style="Action.TButton", command=self.on_select_default_doc).pack(side=tk.LEFT, padx=2)

        # 2. 配置预览卡片
        preview_frame = ttk.LabelFrame(parent, text=" 配置内容可视化解析与预览 (核对设备与通道名) ", padding=8)
        preview_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        cols = ttk.Frame(preview_frame)
        cols.pack(fill=tk.BOTH, expand=True)

        left_col = ttk.Frame(cols)
        left_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 4))
        ttk.Label(left_col, text="【硬件输入/输出绑定】", style="SubHeader.TLabel").pack(anchor="w", pady=(0, 2))
        self.txt_devices = tk.Text(left_col, height=6, wrap=tk.WORD, font=("Consolas", 9), relief=tk.SOLID, bd=1)
        self.txt_devices.pack(fill=tk.BOTH, expand=True)

        right_col = ttk.Frame(cols)
        right_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(4, 0))
        ttk.Label(right_col, text="【自定义通道名称 (Strips/Buses)】", style="SubHeader.TLabel").pack(anchor="w", pady=(0, 2))
        self.txt_labels = tk.Text(right_col, height=6, wrap=tk.WORD, font=("Consolas", 9), relief=tk.SOLID, bd=1)
        self.txt_labels.pack(fill=tk.BOTH, expand=True)

        # 3. 一键应用主按钮卡片
        action_box = ttk.LabelFrame(parent, text=" 🚀 配置生效、菜单预设与备份 ", padding=8)
        action_box.pack(fill=tk.X, pady=(0, 6))

        main_btns = ttk.Frame(action_box)
        main_btns.pack(fill=tk.X)

        btn_apply = tk.Button(
            main_btns,
            text="🚀 一键应用并载入配置到 VoiceMeeter",
            font=("Microsoft YaHei", 10, "bold"),
            bg="#1976D2",
            fg="#FFFFFF",
            activebackground="#0D47A1",
            activeforeground="#FFFFFF",
            relief=tk.FLAT,
            padx=10,
            pady=6,
            cursor="hand2",
            command=self.on_apply_config
        )
        self.btn_apply_config = btn_apply
        btn_apply.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        btn_menu_def = tk.Button(
            main_btns,
            text="☑️ 设置菜单打勾项（自动重启/自启/托盘）",
            font=("Microsoft YaHei", 9, "bold"),
            bg="#0288D1",
            fg="#FFFFFF",
            activebackground="#01579B",
            activeforeground="#FFFFFF",
            relief=tk.FLAT,
            padx=8,
            pady=6,
            cursor="hand2",
            command=self.on_apply_menu_defaults
        )
        btn_menu_def.pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(
            main_btns,
            text="💾 备份/导出当前配置",
            style="Action.TButton",
            command=self.on_export_config
        ).pack(side=tk.LEFT, padx=2)

        # 4. 【专门重构】开机自启设置专区 (防杀软拦截模式)
        autostart_box = ttk.LabelFrame(parent, text=" 🛡️ VoiceMeeter 自身的开机启动（不会让本工具开机运行） ", padding=8)
        autostart_box.pack(fill=tk.X)

        # 状态指示
        status_row = ttk.Frame(autostart_box)
        status_row.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(status_row, text="当前开机自启状态:", style="SubHeader.TLabel").pack(side=tk.LEFT, padx=(0, 6))
        self.lbl_autostart_indicator = ttk.Label(status_row, text="检测中...", style="Normal.TLabel")
        self.lbl_autostart_indicator.pack(side=tk.LEFT)

        # 核心控制按钮
        btn_row = ttk.Frame(autostart_box)
        btn_row.pack(fill=tk.X, pady=(2, 4))

        btn_set_auto = tk.Button(
            btn_row,
            text="检查并补齐 VoiceMeeter 隐藏自启动",
            font=("Microsoft YaHei", 9, "bold"),
            bg="#2E7D32",
            fg="#FFFFFF",
            activebackground="#1B5E20",
            activeforeground="#FFFFFF",
            relief=tk.FLAT,
            padx=10,
            pady=4,
            cursor="hand2",
            command=self.on_enable_autostart
        )
        self.btn_enable_autostart = btn_set_auto
        btn_set_auto.pack(side=tk.LEFT, padx=(0, 8))

        btn_del_auto = tk.Button(
            btn_row,
            text="🔄 重新检测 VoiceMeeter 自启",
            font=("Microsoft YaHei", 9),
            bg="#F5F5F5",
            fg="#D32F2F",
            relief=tk.SOLID,
            bd=1,
            padx=8,
            pady=3,
            cursor="hand2",
            command=self.refresh_vm_status
        )
        btn_del_auto.pack(side=tk.LEFT, padx=(0, 8))

        ttk.Button(
            btn_row,
            text="📁 打开开机启动文件夹 (查看快捷方式)",
            style="Action.TButton",
            command=self.on_open_startup_dir
        ).pack(side=tk.LEFT)

        # 安全性说明文本
        safe_tip = (
            "检测 VoiceMeeter 自己的启动快捷方式和 Run 启动项；已启用且隐藏时不重复设置。\n"
            "只在缺少启动项时补建；已有但未隐藏时补 -h。隐藏主窗口不代表跳过厂商授权提示。"
        )
        ttk.Label(autostart_box, text=safe_tip, foreground="#616161", style="Normal.TLabel").pack(anchor="w", pady=(4, 0))

        self.on_xml_changed()
        self.current_xml_path.trace_add("write", lambda *args: self.on_xml_changed())

    # --------------------------------------------------------------------------
    # 分类 2: 【翡翠绿】系统声音默认设备预设
    # --------------------------------------------------------------------------
    def setup_page_audio(self, parent):
        self.create_category_banner(
            parent, 1,
            "🔊 分类二：系统声音默认设备预设（对齐截图）",
            "自动检测当前系统音频，一键将播放设为 Voicemeeter Input，录制设为 Voicemeeter Out B1。"
        )

        info_frame = ttk.LabelFrame(parent, text=" 🎯 目标系统声音预设说明 (对齐图片截图) ", padding=10)
        info_frame.pack(fill=tk.X, pady=(0, 8))

        desc = (
            "根据您的截图，Windows 系统音频设备需满足以下配置：\n"
            "• 默认播放设备 (Playback) 必须设为:  Voicemeeter Input (VB-Audio Voicemeeter VAIO)\n"
            "• 默认录制设备 (Recording) 必须设为:  Voicemeeter Out B1 (VB-Audio Voicemeeter VAIO)"
        )
        ttk.Label(info_frame, text=desc, style="Normal.TLabel", justify="left").pack(anchor="w")

        status_box = ttk.LabelFrame(parent, text=" 📊 当前系统默认设备检测 ", padding=10)
        status_box.pack(fill=tk.X, pady=(0, 10))

        grid = ttk.Frame(status_box)
        grid.pack(fill=tk.X)

        ttk.Label(grid, text="当前默认播放设备:", style="SubHeader.TLabel").grid(row=0, column=0, sticky="w", pady=4)
        self.lbl_cur_playback = ttk.Label(grid, text="检测中...", style="Normal.TLabel")
        self.lbl_cur_playback.grid(row=0, column=1, sticky="w", padx=8, pady=4)

        ttk.Label(grid, text="当前默认录制设备:", style="SubHeader.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        self.lbl_cur_record = ttk.Label(grid, text="检测中...", style="Normal.TLabel")
        self.lbl_cur_record.grid(row=1, column=1, sticky="w", padx=8, pady=4)

        self.lbl_cur_playback.configure(wraplength=580)
        self.lbl_cur_record.configure(wraplength=580)
        self.lbl_audio_diagnostic = ttk.Label(status_box, text="通过 Windows Core Audio 直接读取，不依赖外部音频助手。",
                                            style="Normal.TLabel", wraplength=760)
        self.lbl_audio_diagnostic.pack(anchor="w", pady=(6, 0))
        ttk.Button(status_box, text="📋 复制检测详情", command=self.on_copy_audio_diagnostics).pack(anchor="e", pady=(4, 0))
        self.audio_diagnostics = "尚未检测"

        btn_frame = ttk.LabelFrame(parent, text=" 🎯 一键预设与系统控制 ", padding=10)
        btn_frame.pack(fill=tk.X, pady=(0, 10))

        btn_set_audio = tk.Button(
            btn_frame,
            text="🎯 一键将系统声音设为预设值（对齐截图）",
            font=("Microsoft YaHei", 10, "bold"),
            bg="#2E7D32",
            fg="#FFFFFF",
            activebackground="#1B5E20",
            activeforeground="#FFFFFF",
            relief=tk.FLAT,
            padx=12,
            pady=6,
            cursor="hand2",
            command=self.on_set_system_audio
        )
        btn_set_audio.pack(fill=tk.X, pady=(0, 8))

        sub_btns = ttk.Frame(btn_frame)
        sub_btns.pack(fill=tk.X)

        ttk.Button(
            sub_btns,
            text="🎛️ 打开系统声音控制面板 (mmsys.cpl)",
            style="Action.TButton",
            command=self.on_open_sound_cpl
        ).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(
            sub_btns,
            text="🔄 重新检测声音设备",
            style="Action.TButton",
            command=self.refresh_audio_status
        ).pack(side=tk.LEFT)

    # --------------------------------------------------------------------------
    # 分类 3: 【活力橙】谷歌 Chrome 优化设置
    # --------------------------------------------------------------------------
    def setup_page_chrome(self, parent):
        self.create_category_banner(
            parent, 2,
            "🌐 分类三：设置谷歌浏览器配置，实现浏览器之间互通音频，解决gpt不能翻译的问题。",
            "配合立体音检查音频互通；是否恢复 GPT 翻译还取决于网站权限、设备选择和音频路由。"
        )

        # 1. 当前活动/最后打开的浏览器检测卡片
        active_box = ttk.LabelFrame(parent, text=" 🎯 最近使用的 Chrome 多开账号（根据浏览器记录识别） ", padding=10)
        active_box.pack(fill=tk.X, pady=(0, 8))

        agrid = ttk.Frame(active_box)
        agrid.pack(fill=tk.X)

        ttk.Label(agrid, text="Chrome 运行状态:", style="SubHeader.TLabel").grid(row=0, column=0, sticky="w", pady=2)
        self.lbl_chrome_running = ttk.Label(agrid, text="检测中...", style="Normal.TLabel")
        self.lbl_chrome_running.grid(row=0, column=1, sticky="w", padx=8, pady=2)

        ttk.Label(agrid, text="最后使用的多开账号:", style="SubHeader.TLabel").grid(row=1, column=0, sticky="w", pady=2)
        self.lbl_chrome_active_profile = ttk.Label(agrid, text="检测中...", style="Normal.TLabel")
        self.lbl_chrome_active_profile.grid(row=1, column=1, sticky="w", padx=8, pady=2)

        tip_lbl = ttk.Label(
            active_box,
            text="💡 先找回旧版音频设置，再关闭自动调音量。截图里的 M154 和 M155 都属于第①项。\n"
                 "同一个 Chrome 用户数据目录共享这些设置；独立用户目录、其他浏览器需分别设置。",
            foreground="#E65100",
            style="Normal.TLabel",
            justify="left", wraplength=800
        )
        tip_lbl.pack(anchor="w", pady=(4, 0))

        # 2. 核心操作：在最后打开的浏览器中一键打开全部 3 项设置
        action_box = ttk.LabelFrame(parent, text=" ⚡ 打开设置页面（按下方对照表手动选择） ", padding=10)
        action_box.pack(fill=tk.X, pady=(0, 8))

        top_btn_row = ttk.Frame(action_box)
        top_btn_row.pack(fill=tk.X, pady=(0, 4))

        btn_open_all = tk.Button(
            top_btn_row,
            text="🎯 打开最近使用的多开账号 · 音频设置页面",
            font=("Microsoft YaHei", 10, "bold"),
            bg="#E65100",
            fg="#FFFFFF",
            activebackground="#BF360C",
            activeforeground="#FFFFFF",
            relief=tk.FLAT,
            padx=12,
            pady=8,
            cursor="hand2",
            command=lambda: self.on_open_flags_in_browser("all")
        )
        btn_open_all.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        btn_copy_url = tk.Button(
            top_btn_row,
            text="📋 复制网址 (chrome://flags)",
            font=("Microsoft YaHei", 9),
            bg="#37474F",
            fg="#FFFFFF",
            activebackground="#263238",
            activeforeground="#FFFFFF",
            relief=tk.FLAT,
            padx=10,
            pady=8,
            cursor="hand2",
            command=lambda: self.on_copy_text("chrome://flags")
        )
        btn_copy_url.pack(side=tk.LEFT)

        # 3. 清晰直观的 3 步设置对照表
        guide_box = ttk.LabelFrame(parent, text=" 📋 3 项设置快速对照表 (打开后对照修改即可) ", padding=10)
        guide_box.pack(fill=tk.X, pady=(0, 8))

        # 项目 1: 延期实验标志
        row1 = ttk.Frame(guide_box)
        row1.pack(fill=tk.X, pady=3)
        ttk.Label(row1, text="① 找回旧版音频设置：", font=("Microsoft YaHei", 9, "bold"), width=28).pack(side=tk.LEFT)
        ttk.Label(row1, text="➔ M154、M155 都选【Enabled】", foreground="#2E7D32", font=("Microsoft YaHei", 9, "bold")).pack(side=tk.LEFT, padx=6)
        ttk.Button(row1, text="🔗 单独打开", style="Action.TButton", command=lambda: self.on_open_flags_in_browser("unexpire")).pack(side=tk.RIGHT)
        ttk.Button(row1, text="📋 复制搜索词", style="Action.TButton", command=lambda: self.on_copy_text("temporary-unexpire-flags")).pack(side=tk.RIGHT, padx=4)

        ttk.Label(guide_box, text="对应截图：Temporarily unexpire M154 / M155 flags。它只让部分旧选项重新出现，\n不是立体声开关；各版本可用项不同。找不到时先看第②项是否仍存在。",
                  style="Normal.TLabel", foreground="#555555", wraplength=800).pack(anchor="w", pady=(0, 5))

        # 项目 2: 麦克风自动增益
        row2 = ttk.Frame(guide_box)
        row2.pack(fill=tk.X, pady=3)
        ttk.Label(row2, text="② 关闭麦克风自动调音量：", font=("Microsoft YaHei", 9, "bold"), width=28).pack(side=tk.LEFT)
        ttk.Label(row2, text="➔ 下拉菜单选择【 Disabled 】", foreground="#D32F2F", font=("Microsoft YaHei", 9, "bold")).pack(side=tk.LEFT, padx=6)
        ttk.Button(row2, text="🔗 单独打开", style="Action.TButton", command=lambda: self.on_open_flags_in_browser("webrtc")).pack(side=tk.RIGHT)
        ttk.Button(row2, text="📋 复制搜索词", style="Action.TButton", command=lambda: self.on_copy_text("enable-webrtc-allow-input-volume-adjustment")).pack(side=tk.RIGHT, padx=4)

        # 项目 3: 全局回声消除
        row3 = ttk.Frame(guide_box)
        row3.pack(fill=tk.X, pady=3)
        ttk.Label(row3, text="③ 关闭浏览器回声消除：", font=("Microsoft YaHei", 9, "bold"), width=28).pack(side=tk.LEFT)
        ttk.Label(row3, text="➔ 下拉菜单选择【 Disabled 】", foreground="#D32F2F", font=("Microsoft YaHei", 9, "bold")).pack(side=tk.LEFT, padx=6)
        ttk.Button(row3, text="🔗 单独打开", style="Action.TButton", command=lambda: self.on_open_flags_in_browser("echo")).pack(side=tk.RIGHT)
        ttk.Button(row3, text="📋 复制搜索词", style="Action.TButton", command=lambda: self.on_copy_text("chrome-wide-echo-cancellation")).pack(side=tk.RIGHT, padx=4)

        step_tip = ttk.Label(
            guide_box,
            text="改完先保存网页工作，再点【Relaunch】重启 Chrome。找不到某项可能是版本已移除；请勿改动其他选项。",
            foreground="#1565C0",
            style="SubHeader.TLabel", wraplength=800
        )
        step_tip.pack(anchor="w", pady=(6, 0))

        # 4. 当前检测状态
        chk_box = ttk.LabelFrame(parent, text=" 📊 当前保存的音频设置（点击刷新读取；不代表正在运行的窗口已生效） ", padding=8)
        chk_box.pack(fill=tk.X, pady=(0, 8))

        cgrid = ttk.Frame(chk_box)
        cgrid.pack(fill=tk.X)

        ttk.Label(cgrid, text="找回旧版音频设置:", style="SubHeader.TLabel").grid(row=0, column=0, sticky="w", pady=2)
        self.lbl_chrome_unexpire = ttk.Label(cgrid, text="检测中...", style="Normal.TLabel")
        self.lbl_chrome_unexpire.grid(row=0, column=1, sticky="w", padx=8, pady=2)

        ttk.Label(cgrid, text="自动调音量:", style="SubHeader.TLabel").grid(row=1, column=0, sticky="w", pady=2)
        self.lbl_chrome_webrtc = ttk.Label(cgrid, text="检测中...", style="Normal.TLabel")
        self.lbl_chrome_webrtc.grid(row=1, column=1, sticky="w", padx=8, pady=2)

        ttk.Label(cgrid, text="回声消除:", style="SubHeader.TLabel").grid(row=2, column=0, sticky="w", pady=2)
        self.lbl_chrome_echo = ttk.Label(cgrid, text="检测中...", style="Normal.TLabel")
        self.lbl_chrome_echo.grid(row=2, column=1, sticky="w", padx=8, pady=2)

        ttk.Button(chk_box, text="🔄 刷新状态与识别", style="Action.TButton", command=self.refresh_chrome_status).pack(anchor="e", pady=(4, 0))

        # 5. 应急保障与安全专区 (多开救援)
        recover_box = ttk.LabelFrame(parent, text=" 🚨 应急救援与安全启动专区 ", padding=10)
        recover_box.pack(fill=tk.X)

        rec_tip = (
            "提示：如果对方之前丢失了桌面多开快捷方式，点击下方【一键批量恢复】即可自动把 160+ 个多开快捷方式全部恢复到桌面！"
        )
        ttk.Label(recover_box, text=rec_tip, foreground="#B71C1C", style="SubHeader.TLabel").pack(anchor="w", pady=(0, 6))

        rec_btns = ttk.Frame(recover_box)
        rec_btns.pack(fill=tk.X)

        btn_rebuild_shortcuts = tk.Button(
            rec_btns,
            text="🔄 一键批量恢复所有 160+ 个多开快捷方式 (桌面)",
            font=("Microsoft YaHei", 9, "bold"),
            bg="#0288D1",
            fg="#FFFFFF",
            activebackground="#01579B",
            activeforeground="#FFFFFF",
            relief=tk.FLAT,
            padx=10,
            pady=5,
            cursor="hand2",
            command=self.on_restore_profile_shortcuts
        )
        btn_rebuild_shortcuts.pack(side=tk.LEFT, padx=(0, 8))

        btn_restore_bak = tk.Button(
            rec_btns,
            text="🚨 还原 Chrome 历史配置备份",
            font=("Microsoft YaHei", 9),
            bg="#757575",
            fg="#FFFFFF",
            activebackground="#424242",
            activeforeground="#FFFFFF",
            relief=tk.FLAT,
            padx=10,
            pady=5,
            cursor="hand2",
            command=self.on_restore_chrome_backup
        )
        btn_restore_bak.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(parent, text="查看并清理旧 Chrome 备份（保留最近 10 份）",
                   command=self.on_clean_chrome_backups).pack(anchor=tk.W, padx=8, pady=4)

        btn_safe_lnk = tk.Button(
            rec_btns,
            text="🛡️ 生成免配置安全快捷方式",
            font=("Microsoft YaHei", 9),
            bg="#2E7D32",
            fg="#FFFFFF",
            activebackground="#1B5E20",
            activeforeground="#FFFFFF",
            relief=tk.FLAT,
            padx=10,
            pady=5,
            cursor="hand2",
            command=self.on_create_safe_shortcut
        )
        btn_safe_lnk.pack(side=tk.LEFT)

    # --------------------------------------------------------------------------
    # 分类 4: 【典雅紫】依赖驱动一键安装
    # --------------------------------------------------------------------------
    def setup_page_drivers(self, parent):
        self.create_category_banner(
            parent, 3,
            "📦 分类四：依赖驱动与软件一键安装（VB-CABLE & VAC Lite）",
            "缺少这两个驱动将导致 VoiceMeeter 的通道2和通道3报错红字，此处提供一键下载安装。"
        )

        ans_box = ttk.LabelFrame(parent, text=" ❓ 为什么必须安装 VB-CABLE 和 VAC Lite 这两个驱动？ ", padding=10)
        ans_box.pack(fill=tk.X, pady=(0, 8))

        why_text = (
            "答：【必须安装，否则配置无法正常工作！】\n\n"
            "正如您的 VoiceMeeter Banana 截图所示：\n"
            "1. 硬件输入第2通道【检测1通道声音】绑定的硬件设备是：CABLE Output (VB-Audio Virtual Cable)\n"
            "2. 硬件输入第3通道【检测2通道声音】绑定的硬件设备是：Line 1 (Virtual Audio Cable)\n\n"
            "如果不安装这两个虚拟声卡驱动，VoiceMeeter 将无法识别这两个通道的输入设备（显示红字），\n"
            "导致无法接收到检测软件或特定应用的声音！"
        )
        ttk.Label(ans_box, text=why_text, style="Normal.TLabel", justify="left").pack(anchor="w")

        pkg_box = ttk.LabelFrame(parent, text=" 软件与驱动一键下载安装 ", padding=10)
        pkg_box.pack(fill=tk.X, pady=(0, 8))

        # 1. VoiceMeeter Banana
        row1 = ttk.Frame(pkg_box)
        row1.pack(fill=tk.X, pady=4)
        ttk.Label(row1, text="1. VoiceMeeter Banana 主程序:", style="SubHeader.TLabel", width=28).pack(side=tk.LEFT)
        ttk.Button(row1, text="验证本地官网安装包", style="Action.TButton", command=self.on_install_vm_local).pack(side=tk.LEFT, padx=3)
        ttk.Button(row1, text="Winget 一键安装", style="Action.TButton", command=self.on_install_vm_winget).pack(side=tk.LEFT, padx=3)

        # 2. VB-CABLE
        row2 = ttk.Frame(pkg_box)
        row2.pack(fill=tk.X, pady=4)
        ttk.Label(row2, text="2. VB-CABLE 虚拟音频线 (通道2):", style="SubHeader.TLabel", width=28).pack(side=tk.LEFT)
        btn_vbcable = tk.Button(
            row2,
            text="📥 官网下载并验证安装 VB-CABLE",
            font=("Microsoft YaHei", 9, "bold"),
            bg="#6A1B9A",
            fg="#FFFFFF",
            relief=tk.FLAT,
            padx=8,
            pady=3,
            cursor="hand2",
            command=self.on_install_vbcable
        )
        btn_vbcable.pack(side=tk.LEFT, padx=3)

        # 3. VAC Lite
        row3 = ttk.Frame(pkg_box)
        row3.pack(fill=tk.X, pady=4)
        ttk.Label(row3, text="3. VAC Lite 虚拟音频线 (通道3):", style="SubHeader.TLabel", width=28).pack(side=tk.LEFT)
        btn_vac = tk.Button(
            row3,
            text="📥 官网下载并验证安装 VAC Lite 4.71",
            font=("Microsoft YaHei", 9, "bold"),
            bg="#6A1B9A",
            fg="#FFFFFF",
            relief=tk.FLAT,
            padx=8,
            pady=3,
            cursor="hand2",
            command=self.on_install_vac
        )
        btn_vac.pack(side=tk.LEFT, padx=3)

        source_box = ttk.LabelFrame(parent, text=" 厂商官网与下载地址（可复制核对） ", padding=8)
        source_box.pack(fill=tk.X, pady=(0, 8))
        for label, url in [
            ("VB-CABLE 官网", "https://vb-audio.com/Cable/"),
            ("VB-CABLE Pack45 下载", vm_manager.URL_VBCABLE),
            ("VAC Lite 官网", "https://vac.muzychenko.net/en/download.htm"),
            ("VAC Lite 4.71 下载", vm_manager.URL_VAC_LITE),
        ]:
            line = ttk.Frame(source_box)
            line.pack(fill=tk.X, pady=2)
            ttk.Label(line, text=label + "：", width=23).pack(side=tk.LEFT)
            address = ttk.Entry(line)
            address.insert(0, url)
            address.configure(state="readonly")
            address.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(source_box, text="安装前验证固定包哈希和厂商签名；验证失败会停止。若仍报毒，请保留检测名称核查，勿关闭防护。",
                  wraplength=800, foreground="#B71C1C").pack(anchor="w", pady=(5, 0))

        # 快捷目录
        dir_box = ttk.LabelFrame(parent, text=" 快捷文件夹入口 ", padding=8)
        dir_box.pack(fill=tk.X)

        ttk.Button(dir_box, text="📁 打开 Documents\\Voicemeeter 配置目录", style="Action.TButton", command=self.on_open_docs_dir).pack(side=tk.LEFT, padx=3)
        ttk.Button(dir_box, text="📁 打开 VoiceMeeter 安装目录", style="Action.TButton", command=self.on_open_install_dir).pack(side=tk.LEFT, padx=3)
        ttk.Button(dir_box, text="📁 打开本工具下载缓存目录", style="Action.TButton", command=self.on_open_cache_dir).pack(side=tk.LEFT, padx=3)

    # --------------------------------------------------------------------------
    # 底部日志
    # --------------------------------------------------------------------------
    def create_footer_log(self):
        foot = ttk.Frame(self.root, padding=(12, 2, 12, 6))
        foot.pack(fill=tk.X)

        self.lbl_log = ttk.Label(foot, text="就绪", foreground="#555555", style="Normal.TLabel")
        self.lbl_log.pack(anchor="w", fill=tk.X)

    def log(self, message):
        if hasattr(self, "lbl_log"):
            self.lbl_log.config(text=message)

    def on_page_scroll(self, event):
        canvas = self.page_canvases[self.current_tab_index]
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        while widget is not None:
            if widget == canvas:
                canvas.yview_scroll(int(-event.delta / 120), "units")
                return "break"
            widget = getattr(widget, "master", None)

    # --------------------------------------------------------------------------
    # 状态刷新
    # --------------------------------------------------------------------------
    def refresh_all_status(self):
        for refresh in (self.refresh_vm_status, self.refresh_audio_status, self.refresh_chrome_status):
            try:
                refresh()
            except Exception as exc:
                self.log(f"状态检测失败：{exc}；未将其当作未安装或未启用。")

    def refresh_vm_status(self):
        self.status_data = vm_manager.get_system_status()

        if self.status_data["installed"]:
            if self.status_data["is_running"] is None:
                self.lbl_vm_status.config(text="⚠ 已安装（运行状态检测失败）", style="StatusWarn.TLabel")
            elif self.status_data["is_running"]:
                self.lbl_vm_status.config(text="✔ 已安装 (● 运行中)", style="StatusSuccess.TLabel")
            else:
                self.lbl_vm_status.config(text="✔ 已安装 (○ 未启动)", style="StatusInfo.TLabel")
        else:
            self.lbl_vm_status.config(text="✖ 未安装", style="StatusWarn.TLabel")

        for check, label in ((vm_manager.check_vbcable_installed, self.lbl_cable_status),
                             (vm_manager.check_vac_installed, self.lbl_vac_status)):
            installed = check()
            if installed is None:
                label.config(text="⚠ 无法检测", style="StatusWarn.TLabel")
            elif installed:
                label.config(text="✔ 已安装", style="StatusSuccess.TLabel")
            else:
                label.config(text="✖ 未安装", style="StatusWarn.TLabel")

        state = self.status_data["autostart_details"]
        if state["enabled"] and state["hidden"]:
            count = sum(e["enabled"] for e in state["entries"])
            text = "✔ VoiceMeeter 已设置隐藏自启动，无需重复设置"
            if count > 1:
                text += f"（发现 {count} 个启动项，请在 Windows 中检查重复项）"
            style = "StatusSuccess.TLabel"
            self.btn_enable_autostart.config(state=tk.DISABLED)
        elif state["enabled"]:
            text, style = "⚠ 已设置自启动，但尚未全部隐藏；可补齐隐藏参数", "StatusWarn.TLabel"
            self.btn_enable_autostart.config(state=tk.NORMAL)
        elif state["enabled"] is None:
            text, style = "⚠ 无法完整检测，未将其当作未设置；请查看日志", "StatusWarn.TLabel"
            self.btn_enable_autostart.config(state=tk.DISABLED)
            self.log("启动项检测失败：" + "; ".join(state["errors"]))
        elif state["entries"]:
            text, style = "⚠ 已有启动项但被 Windows 禁用，请在 Windows 启动设置中开启", "StatusWarn.TLabel"
            self.btn_enable_autostart.config(state=tk.DISABLED)
        else:
            text, style = "○ 未发现 VoiceMeeter 自启动，可点击下方按钮设置", "Normal.TLabel"
            self.btn_enable_autostart.config(state=tk.NORMAL)
        self.lbl_autostart_indicator.config(text=text, style=style, wraplength=760)

    def refresh_audio_status(self):
        res = vm_manager.get_system_audio_status()
        details = ["检测方式：Windows Core Audio（直接调用系统接口，不运行外部助手）"]
        for key, label, title, target in (
            ("playback", self.lbl_cur_playback, "播放", "Voicemeeter Input"),
            ("record", self.lbl_cur_record, "录制", "Voicemeeter Out B1 / Output"),
        ):
            error = res.get(key + "_error")
            # Backward-compatible error handling for an unexpected provider error.
            if not res.get("success") and not any(res.get(k + "_error") for k in ("playback", "record")):
                error = res.get("error") or "系统音频接口未返回有效结果"
            name = res.get(key + "_default", "")
            if error:
                label.config(text="检测失败：" + str(error), style="StatusWarn.TLabel")
                details.append(title + "检测失败：" + str(error))
            elif not name:
                label.config(text="Windows 未设置默认活动设备（请检查连接、禁用状态和音频服务）", style="StatusWarn.TLabel")
                details.append(title + "：没有默认活动设备")
            elif res.get("is_" + key + "_match"):
                label.config(text="✔ " + name + "（已符合预设）", style="StatusSuccess.TLabel")
                details.append(title + "默认设备：" + name)
            else:
                label.config(text="⚠ " + name + "（预设为 " + target + "）", style="StatusWarn.TLabel")
                details.append(title + "默认设备：" + name)
            details.append(title + "活动设备：" + "；".join(res.get(key + "_devices", [])))
        details.extend(res.get("warnings", []))
        self.audio_diagnostics = "\n".join(details)
        errors = [res.get(k + "_error") for k in ("playback", "record") if res.get(k + "_error")]
        diagnostic = "；".join(dict.fromkeys(errors))
        if not diagnostic and not res.get("success"):
            diagnostic = res.get("error") or "音频检测失败，点击复制检测详情查看。"
        if not diagnostic and res.get("warnings"):
            diagnostic = "；".join(res["warnings"])
        self.lbl_audio_diagnostic.config(text=diagnostic or "已通过 Windows Core Audio 读取系统普通默认设备；网站单独选择的设备可能不同。",
                                         style="StatusWarn.TLabel" if diagnostic else "Normal.TLabel")

    def on_copy_audio_diagnostics(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.audio_diagnostics)
        self.log("声音设备检测详情已复制。")

    def refresh_chrome_status(self):
        try:
            self._refresh_chrome_status()
        except Exception as exc:
            self.lbl_chrome_running.config(text="无法检测 Chrome 运行状态", foreground="#B71C1C")
            self.log("Chrome 状态检测失败：" + str(exc))

    def _refresh_chrome_status(self):
        # 1. 刷新活动浏览器信息
        is_running = vm_manager.is_process_running("chrome")
        if hasattr(self, "lbl_chrome_running"):
            if is_running:
                self.lbl_chrome_running.config(text="● 正在运行 (检测到多开实例活跃中)", foreground="#2E7D32")
            else:
                self.lbl_chrome_running.config(text="○ 未运行 (点击一键打开将启动最后活动账号)", foreground="#757575")

        last_profile, friendly_name, last_time = vm_manager.get_last_active_chrome_profile()
        if hasattr(self, "lbl_chrome_active_profile"):
            if last_profile:
                self.lbl_chrome_active_profile.config(
                    text=f"{friendly_name} ({last_profile})  •  识别依据: {last_time}",
                    foreground="#1976D2"
                )
            else:
                self.lbl_chrome_active_profile.config(text="未检测到 Profile", foreground="#757575")

        # 2. 刷新 Flags 配置状态
        res = vm_manager.get_chrome_flags_status()
        if res.get("read_error"):
            for label in (self.lbl_chrome_unexpire, self.lbl_chrome_webrtc, self.lbl_chrome_echo):
                label.config(text="无法读取已保存设置", style="StatusWarn.TLabel")
            self.log("Chrome 配置读取失败：" + res["read_error"])
            return
        if not res["has_chrome"]:
            self.lbl_chrome_unexpire.config(text="未安装 Google Chrome", style="Normal.TLabel")
            self.lbl_chrome_webrtc.config(text="未安装 Google Chrome", style="Normal.TLabel")
            self.lbl_chrome_echo.config(text="未安装 Google Chrome", style="Normal.TLabel")
            return

        if res["unexpire_enabled"]:
            self.lbl_chrome_unexpire.config(text=res.get("unexpire_details", "已启用"), style="StatusSuccess.TLabel")
        else:
            self.lbl_chrome_unexpire.config(text=res.get("unexpire_details", "未设置"), style="StatusWarn.TLabel")

        if res["vol_adjustment_disabled"]:
            self.lbl_chrome_webrtc.config(text="✔ 已禁用 (Disabled - 音量锁定不自动调整)", style="StatusSuccess.TLabel")
        else:
            self.lbl_chrome_webrtc.config(text="⚠ 未禁用 (可能会自动拉升/压低麦克风)", style="StatusWarn.TLabel")

        if res["echo_cancellation_disabled"]:
            self.lbl_chrome_echo.config(text="✔ 已保存 Disabled（重启 Chrome 后检查实际效果）", style="StatusSuccess.TLabel")
        else:
            self.lbl_chrome_echo.config(text="⚠ 未禁用 (可能导致声音被过滤失真)", style="StatusWarn.TLabel")

    # --------------------------------------------------------------------------
    # 分类 1 事件
    # --------------------------------------------------------------------------
    def on_xml_changed(self):
        xml_path = self.current_xml_path.get().strip()
        self.txt_devices.delete("1.0", tk.END)
        self.txt_labels.delete("1.0", tk.END)

        if not xml_path or not os.path.exists(xml_path):
            self.txt_devices.insert(tk.END, "请选择有效的 XML 配置文件以查看详情。")
            self.txt_labels.insert(tk.END, "暂无配置数据。")
            return

        try:
            summary = vm_manager.parse_xml_config(xml_path)
            dev_lines = ["[输入设备 Hardware Inputs]:"]
            if summary["hardware_inputs"]:
                dev_lines.extend(["  " + item for item in summary["hardware_inputs"]])
            else:
                dev_lines.append("  (未指定硬件输入)")

            dev_lines.append("\n[输出设备 Hardware Outputs]:")
            if summary["hardware_outputs"]:
                dev_lines.extend(["  " + item for item in summary["hardware_outputs"]])
            else:
                dev_lines.append("  (未指定硬件输出)")

            self.txt_devices.insert(tk.END, "\n".join(dev_lines))

            label_lines = ["[输入通道名称 (Strips)] :"]
            for strip in summary.get("named_strips", []):
                label_lines.append(f"  {strip}")

            label_lines.append("\n[输出总线名称 (Buses)] :")
            for bus in summary.get("named_buses", []):
                label_lines.append(f"  {bus}")

            self.txt_labels.insert(tk.END, "\n".join(label_lines))
            self.log(f"已加载配置文件: {os.path.basename(xml_path)} ({summary['file_size']} 字节)")
        except Exception as e:
            self.txt_devices.insert(tk.END, f"解析失败: {str(e)}")
            self.txt_labels.insert(tk.END, "格式不兼容或损坏。")

    def on_browse_xml(self):
        initial_dir = vm_manager.DOCS_VM_DIR if os.path.exists(vm_manager.DOCS_VM_DIR) else os.path.dirname(__file__)
        filename = filedialog.askopenfilename(
            title="选择 VoiceMeeter XML 配置文件",
            initialdir=initial_dir,
            filetypes=[("VoiceMeeter XML 配置文件", "*.xml"), ("所有文件", "*.*")]
        )
        if filename:
            self.current_xml_path.set(filename)

    def on_select_sample_config(self):
        sample = vm_manager.get_resource_path("sample_config.xml")
        if os.path.exists(sample):
            self.current_xml_path.set(sample)
        else:
            messagebox.showinfo("提示", "未找到 sample_config.xml")

    def on_select_default_doc(self):
        last_xml = os.path.join(vm_manager.DOCS_VM_DIR, "VoicemeeterBanana_LastSettings.xml")
        if os.path.exists(last_xml):
            self.current_xml_path.set(last_xml)
        else:
            messagebox.showinfo("提示", "未找到系统文档默认配置。")

    def confirm_config_action(self, xml_path, title):
        try:
            review = inspect_config_file(xml_path)
        except (OSError, ValueError) as exc:
            messagebox.showerror("配置无法读取", str(exc))
            return None
        message = "将固定选中的 XML，自动退出 VoiceMeeter（超时后强制结束）、保存、隐藏重新打开并核验配置。\n音频会短暂中断，未保存的临时调整会被选中配置替换。"
        if review["network_risks"]:
            message += "\n\n【此文件的网络音频/控制设置】\n" + "\n".join(review["network_risks"])
            message += "\n仅在你确认这些地址和网络用途时继续；不会擅自删除 VBAN 功能。"
        if not messagebox.askyesno(title, message + "\n\n是否继续？"):
            return None
        return {"allow_network_config": bool(review["network_risks"]), "expected_config_hash": review["sha256"]}

    def on_apply_config(self):
        xml_path = self.current_xml_path.get().strip()
        if not xml_path or not os.path.exists(xml_path):
            messagebox.showwarning("警告", "请先选择一个有效的 XML 配置文件！")
            return

        approval = self.confirm_config_action(xml_path, "应用配置并重新启动立体音")
        if approval is None:
            return
        self.log("正在应用配置到 VoiceMeeter...")
        self.btn_apply_config.config(state=tk.DISABLED)
        def run():
            def progress(message):
                self.root.after(0, lambda text=message: self.log(text))
            success, msg = vm_manager.apply_xml_config(xml_path, restart_app=True, set_as_default=True, progress_callback=progress, **approval)
            self.root.after(0, lambda: self._apply_finished(success, msg))

        threading.Thread(target=run, daemon=True).start()

    def _apply_finished(self, success, msg):
        self.btn_apply_config.config(state=tk.NORMAL)
        self.refresh_all_status()
        self.log(msg)
        if success:
            messagebox.showinfo("成功", f"🎉 配置应用成功！\n\n{msg}")
        else:
            messagebox.showerror("失败", f"配置应用失败：\n{msg}")

    def on_apply_menu_defaults(self):
        """一键应用 VoiceMeeter 菜单打勾预设项"""
        xml_path = self.current_xml_path.get().strip()
        approval = self.confirm_config_action(xml_path, "一键设置默认菜单项")
        if approval is None:
            return
        self.log("正在应用 VoiceMeeter 菜单打勾预设...")
        def run():
            success, msg = vm_manager.configure_voicemeeter_menu_defaults(xml_path, **approval)
            self.root.after(0, lambda: self._menu_defaults_finished(success, msg))
        threading.Thread(target=run, daemon=True).start()

    def _menu_defaults_finished(self, success, msg):
        self.refresh_vm_status()
        self.log(msg.replace("\n", " "))
        if success:
            messagebox.showinfo("菜单预设已生效", msg)
        else:
            messagebox.showerror("设置失败", msg)

    def on_enable_autostart(self):
        self.log("正在检查 VoiceMeeter 自身的隐藏自启动...")
        success, msg = vm_manager.configure_autostart(enable=True)
        self.refresh_vm_status()
        self.log(msg.replace("\n", " "))
        if success:
            messagebox.showinfo("VoiceMeeter 自启动", msg)
        else:
            messagebox.showerror("未完成设置", msg)

    def on_open_startup_dir(self):
        """打开系统开机启动文件夹"""
        vm_manager.open_startup_dir()

    def on_export_config(self):
        save_file = filedialog.asksaveasfilename(
            title="导出保存当前配置",
            defaultextension=".xml",
            filetypes=[("XML 配置文件", "*.xml")]
        )
        if not save_file:
            return
        success, msg = vm_manager.export_current_config(save_file)
        self.log(msg)
        if success:
            messagebox.showinfo("导出成功", msg)
        else:
            messagebox.showerror("导出失败", msg)

    # --------------------------------------------------------------------------
    # 顶部动作
    # --------------------------------------------------------------------------
    def on_launch_app(self):
        self.log("正在启动 VoiceMeeter...")
        xml_path = self.current_xml_path.get().strip()
        def run():
            success, msg = vm_manager.launch_voicemeeter(xml_path if os.path.exists(xml_path) else None)
            self.root.after(0, lambda: self.log(msg))
            self.root.after(800, self.refresh_vm_status)
        threading.Thread(target=run, daemon=True).start()

    def on_kill_app(self):
        self.log("正在关闭 VoiceMeeter...")
        def run():
            success, msg = vm_manager.kill_voicemeeter()
            self.root.after(0, lambda: self.log(msg))
            self.root.after(500, self.refresh_vm_status)
        threading.Thread(target=run, daemon=True).start()

    def on_restart_engine(self):
        self.log("正在重启音频引擎...")
        def run():
            success, msg = vm_manager.restart_audio_engine()
            self.root.after(0, lambda: self.log(msg))
        threading.Thread(target=run, daemon=True).start()

    # --------------------------------------------------------------------------
    # 分类 2 事件 (声音)
    # --------------------------------------------------------------------------
    def on_set_system_audio(self):
        self.log("正在设置系统默认声音设备...")
        def run():
            success, msg = vm_manager.set_system_default_audio(set_playback=True, set_record=True)
            self.root.after(0, lambda: self._set_audio_finished(success, msg))
        threading.Thread(target=run, daemon=True).start()

    def _set_audio_finished(self, success, msg):
        self.refresh_audio_status()
        self.log("系统声音设置完成！")
        if success:
            messagebox.showinfo("设置成功", f"🎉 系统声音已按截图完成设置！\n\n{msg}")
        else:
            messagebox.showwarning("设置提示", f"部分设备设置结果如下：\n\n{msg}")

    def on_open_sound_cpl(self):
        vm_manager.open_sound_control_panel()

    # --------------------------------------------------------------------------
    # 分类 3 事件 (Chrome)
    # --------------------------------------------------------------------------
    def on_clean_chrome_backups(self):
        try:
            directory = vm_manager.get_chrome_user_data_path()
            plan = cleanup_plan(directory)
            if not plan:
                messagebox.showinfo('备份清理', '没有可清理的旧备份。保留最近 10 份、30 天内备份和并发冲突恢复文件。')
                return
            size = sum(item['size'] for item in plan) / (1024 * 1024)
            if not messagebox.askyesno('确认清理旧备份', f'将删除 {len(plan)} 份超过 30 天的旧 Chrome 备份，共 {size:.1f} MiB。\n保留最近 10 份和冲突恢复文件。\n删除后这些旧版本无法还原，是否继续？'):
                return
            count = apply_cleanup(directory, plan)
            messagebox.showinfo('备份清理', f'已清理 {count} 份旧备份。')
        except Exception as exc:
            messagebox.showerror('备份清理停止', str(exc))

    def on_restore_chrome_backup(self):
        """一键从备份还原 Chrome 原始 Local State"""
        backups = vm_manager.get_chrome_backups()
        if not backups:
            messagebox.showinfo("提示", "未找到任何 Chrome 历史备份文件。")
            return

        latest_name = backups[0][0]
        confirm = messagebox.askyesno(
            "确认恢复 Chrome 备份",
            f"将从备份文件还原原始 Local State 配置：\n\n"
            f"目标备份: {latest_name}\n\n"
            "此操作将恢复原有的所有多开环境及系统设置。\n"
            "是否继续？"
        )
        if not confirm:
            return

        self.log("正在恢复 Chrome 原始备份...")
        def run():
            success, msg = vm_manager.restore_chrome_backup(backups[0][1])
            self.root.after(0, lambda: self._restore_finished(success, msg))
        threading.Thread(target=run, daemon=True).start()

    def _restore_finished(self, success, msg):
        self.refresh_chrome_status()
        self.log(msg.replace("\n", " "))
        if success:
            messagebox.showinfo("恢复成功", msg)
        else:
            messagebox.showerror("恢复失败", msg)

    def on_restore_profile_shortcuts(self):
        """一键批量恢复所有多开桌面快捷方式"""
        confirm = messagebox.askyesno(
            "恢复多开快捷方式",
            "本功能将自动读取 Chrome 的所有多开 Profile 账号配置，\n"
            "并在桌面生成【Chrome多开快捷方式】文件夹，批量重建所有账号的快捷方式。\n\n"
            "是否立即生成？"
        )
        if not confirm:
            return

        self.log("正在扫描并批量生成多开快捷方式...")
        def run():
            success, msg = vm_manager.restore_chrome_profile_shortcuts()
            self.root.after(0, lambda: self._profile_shortcuts_finished(success, msg))
        threading.Thread(target=run, daemon=True).start()

    def _profile_shortcuts_finished(self, success, msg):
        self.log(msg.replace("\n", " "))
        if success:
            open_it = messagebox.askyesno("生成成功", f"{msg}\n\n是否立即打开该文件夹查看？")
            if open_it:
                desktop_dir = os.path.expanduser("~/Desktop")
                target_dir = os.path.join(desktop_dir, "Chrome多开快捷方式")
                if os.path.exists(target_dir):
                    os.startfile(target_dir)
        else:
            messagebox.showerror("生成失败", msg)

    def on_create_safe_shortcut(self):
        """生成免改配置安全快捷方式"""
        self.log("正在创建 Chrome 安全快捷方式...")
        def run():
            success, msg = vm_manager.create_chrome_safe_shortcut()
            self.root.after(0, lambda: self._safe_shortcut_finished(success, msg))
        threading.Thread(target=run, daemon=True).start()

    def _safe_shortcut_finished(self, success, msg):
        self.log(msg.replace("\n", " "))
        if success:
            messagebox.showinfo("快捷方式已创建", msg)
        else:
            messagebox.showerror("创建失败", msg)

    def on_open_flags_in_browser(self, flag_type="all"):
        """在最后打开的浏览器中打开实验标志设置页签"""
        self.log(f"正在最后打开的浏览器中打开 chrome://flags ({flag_type})...")
        target_url = vm_manager.chrome_flags_url(flag_type)
        # 复制对应的定位网址，浏览器策略拦截时可直接粘贴
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(target_url)
        except Exception:
            pass

        success, msg = vm_manager.open_chrome_audio_flags_in_last_browser(flag_type)
        self.log(msg.replace("\n", " "))
        self.refresh_chrome_status()
        if not success:
            messagebox.showerror("打开失败", msg)

    def on_copy_text(self, text):
        """复制搜索词到剪贴板"""
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.log(f"已复制到剪贴板: {text}")
            messagebox.showinfo("已复制", f"已成功复制搜索词到剪贴板：\n\n{text}\n\n可在 Chrome 实验页面的搜索框中直接 Ctrl+V 粘贴定位。")
        except Exception as e:
            self.log(f"复制失败: {str(e)}")

    def on_apply_chrome_flags(self):
        if vm_manager.is_process_running("chrome"):
            messagebox.showwarning(
                "请先手动退出 Chrome",
                "⚠ 检测到 Chrome 正在运行中！\n\n"
                "为了彻底保障您的多开窗口和账号数据安全，本工具已禁止强制终止 Chrome。\n\n"
                "请先手动关闭所有 Chrome 窗口后再点击应用；\n"
                "或者直接使用【生成免调整安全快捷方式】（推荐，无需退出浏览器）。"
            )
            return

        confirm = messagebox.askyesno(
            "确认写入配置",
            "将修改 Chrome Local State 启用延期 Flags 并禁用麦克风自动增益与回声消除。\n"
            "（修改前工具会自动进行安全备份）\n\n是否继续？"
        )
        if not confirm:
            return

        self.log("正在配置 Chrome Flags...")
        def run():
            success, msg = vm_manager.apply_chrome_audio_flags(kill_chrome_first=False)
            self.root.after(0, lambda: self._apply_chrome_finished(success, msg))

        threading.Thread(target=run, daemon=True).start()

    def _apply_chrome_finished(self, success, msg):
        self.refresh_chrome_status()
        self.log("Chrome 配置完成！")
        if success:
            messagebox.showinfo("配置成功", msg)
        else:
            messagebox.showerror("配置失败", msg)

    def on_launch_chrome_with_args(self):
        success, msg = vm_manager.launch_chrome_with_audio_flags()
        self.log(msg)

    def on_open_chrome_flags(self):
        import webbrowser
        webbrowser.open("chrome://flags/#enable-webrtc-allow-input-volume-adjustment")

    # --------------------------------------------------------------------------
    # 分类 4 事件 (驱动安装)
    # --------------------------------------------------------------------------
    def on_install_vm_local(self):
        installer = filedialog.askopenfilename(
            title="选择 VoiceMeeter 安装程序 (VoicemeeterProSetup.exe)",
            filetypes=[("可执行程序", "*.exe"), ("所有文件", "*.*")]
        )
        if not installer:
            return
        self.log(f"正在安装: {installer} ...")
        def run():
            success, msg = vm_manager.install_voicemeeter(installer, method="local")
            self.root.after(0, lambda: self._install_finished(success, msg))
        threading.Thread(target=run, daemon=True).start()

    def on_install_vm_winget(self):
        confirm = messagebox.askyesno("确认", "是否使用 Windows Winget 自动安装 VoiceMeeter？")
        if not confirm:
            return
        self.log("正在使用 Winget 安装 VoiceMeeter...")
        def run():
            success, msg = vm_manager.install_voicemeeter(method="winget")
            self.root.after(0, lambda: self._install_finished(success, msg))
        threading.Thread(target=run, daemon=True).start()

    def on_install_vbcable(self):
        confirm = messagebox.askyesno(
            "确认安装 VB-CABLE",
            "将从官方下载并安装 VB-Audio Virtual Cable 驱动包。\n"
            "安装过程将弹出管理员权限请求 (UAC)。\n\n是否继续？"
        )
        if not confirm:
            return
        self.log("正在下载并准备安装 VB-CABLE...")
        def run():
            success, msg = vm_manager.install_vbcable(progress_callback=lambda m: self.root.after(0, lambda: self.log(m)))
            self.root.after(0, lambda: self._install_finished(success, msg))
        threading.Thread(target=run, daemon=True).start()

    def on_install_vac(self):
        confirm = messagebox.askyesno(
            "确认安装 VAC Lite",
            "将从官方下载 Virtual Audio Cable Lite 驱动包并启动官方安装向导。\n\n"
            "提示：启动向导后，只需在向导窗口点击 [I accept] 和 [Install] 即可顺利完成！\n\n"
            "是否继续？"
        )
        if not confirm:
            return
        self.log("正在下载并准备启动 VAC Lite 官方安装向导...")
        def run():
            success, msg = vm_manager.install_vac(progress_callback=lambda m: self.root.after(0, lambda: self.log(m)))
            self.root.after(0, lambda: self._install_finished(success, msg))
        threading.Thread(target=run, daemon=True).start()

    def _install_finished(self, success, msg):
        self.refresh_all_status()
        self.log(msg.replace("\n", " "))
        if success:
            messagebox.showinfo("安装提示", msg)
        else:
            messagebox.showerror("安装失败", msg)

    def on_open_docs_dir(self):
        if os.path.exists(vm_manager.DOCS_VM_DIR):
            os.startfile(vm_manager.DOCS_VM_DIR)
        else:
            messagebox.showinfo("提示", "文档目录尚未创建。")

    def on_open_install_dir(self):
        if os.path.exists(vm_manager.VOICEMEETER_DIR_X86):
            os.startfile(vm_manager.VOICEMEETER_DIR_X86)
        else:
            messagebox.showinfo("提示", "未找到安装目录。")

    def on_open_cache_dir(self):
        os.makedirs(vm_manager.CACHE_DIR, exist_ok=True)
        os.startfile(vm_manager.CACHE_DIR)


def main():
    root = tk.Tk()
    app = VoiceMeeterManagerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
