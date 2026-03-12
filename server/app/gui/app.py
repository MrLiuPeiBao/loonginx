"""Tkinter monitoring GUI for the server."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import threading
import tkinter as tk
import time
from tkinter import filedialog
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk
from typing import Any, Dict, List, Optional

import requests
import numpy as np
import librosa
import matplotlib as mpl
from PIL import Image, ImageTk
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# 确保中文字体可用，避免图例/坐标显示方框
mpl.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial Unicode MS', 'sans-serif']
mpl.rcParams['axes.unicode_minus'] = False

from app.core.config import get_settings, load_env
from app.services.data_service import SENSOR_VALUE_FIELDS
from app.services.cableway_specs import CONTROL_COMMANDS, FLOAT_PARAMS
from app.gui.audio_monitor import AudioRTSPMonitor
from app.gui.config_options import CONFIG_SECTIONS

logger = logging.getLogger(__name__)


class _GUILogHandler(logging.Handler):
    """Logging handler forwarding records into the GUI log panel."""

    def __init__(self, gui: 'MonitoringGUI') -> None:
        super().__init__(level=logging.INFO)
        self._gui = gui
        self.setFormatter(logging.Formatter('[%(levelname)s] %(name)s: %(message)s'))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:  # pragma: no cover - defensive
            return
        self._gui.append_log_from_thread(message)


class MonitoringGUI:
    """Main GUI application."""

    REFRESH_INTERVAL_MS = 6000

    def __init__(self) -> None:
        load_env()
        self.settings = get_settings()
        self.base_url = self._build_base_url()

        self.root = tk.Tk()
        self.root.title('Sensor Monitoring Dashboard')
        self.root.geometry('1100x750')

        self.status_var = tk.StringVar()
        self.command_payload = tk.StringVar()
        self.command_notes = tk.StringVar()
        self.command_device = tk.StringVar()
        self.command_request_id = tk.StringVar(value=self._generate_request_id())

        self.sensor_tree: Optional[ttk.Treeview] = None
        self.bms_tree: Optional[ttk.Treeview] = None
        self.rfid_tree: Optional[ttk.Treeview] = None
        self.cableway_tree: Optional[ttk.Treeview] = None
        self.alarm_tree: Optional[ttk.Treeview] = None
        self.config_tree: Optional[ttk.Treeview] = None
        self.images_tree: Optional[ttk.Treeview] = None
        self.command_history: Optional[scrolledtext.ScrolledText] = None
        self.log_text: Optional[scrolledtext.ScrolledText] = None
        self.cableway_detail_text: Optional[scrolledtext.ScrolledText] = None
        self.cableway_device = tk.StringVar()
        self.cableway_request_id = tk.StringVar(value=self._generate_request_id())
        self.cableway_notes = tk.StringVar()
        self.cableway_param_key = tk.StringVar()
        self.cableway_param_value = tk.StringVar()
        self.cableway_param_hint = tk.StringVar()
        self.playback_url = tk.StringVar()
        self.playback_proc: Optional[subprocess.Popen] = None
        self.playback_proc_external: Optional[subprocess.Popen] = None
        self.image_preview_label: Optional[ttk.Label] = None
        self.config_form_vars: Dict[str, tk.StringVar] = {}
        self._config_cache: Dict[str, Dict[str, Any]] = {}
        self.runtime_config_vars: Dict[str, tk.Variable] = {}
        self._runtime_option_meta: Dict[str, Dict[str, Any]] = {}
        self._runtime_options_hash: Optional[str] = None
        self._runtime_action_busy = 0
        self._runtime_action_buttons: Dict[str, ttk.Button] = {}
        self._runtime_action_labels: Dict[str, ttk.Label] = {}
        self._runtime_option_widgets: Dict[str, Any] = {}
        self._runtime_history: Dict[str, List[str]] = {}
        self._runtime_history_path = Path(__file__).resolve().parents[2] / 'logs' / 'runtime_config_history.json'
        self._image_records: List[Dict[str, Any]] = []
        self._image_cache: Dict[str, Dict[str, Any]] = {}
        self._cableway_cache: Dict[str, Dict[str, Any]] = {}
        self._current_image_photo: Optional[ImageTk.PhotoImage] = None
        self._sensor_type_options: List[str] = sorted(set(SENSOR_VALUE_FIELDS))
        self._sensor_type_combo: Optional[ttk.Combobox] = None
        self.audio_threshold_vars: Dict[str, tk.StringVar] = {}
        self.audio_fig: Optional[Figure] = None
        self.audio_canvas: Optional[FigureCanvasTkAgg] = None
        self.audio_lines: Dict[str, Any] = {}
        self._audio_metrics_buffer: List[Dict[str, float]] = []
        self.audio_tree: Optional[ttk.Treeview] = None
        self._audio_records: List[Dict[str, Any]] = []
        self._audio_play_proc: Optional[subprocess.Popen] = None
        self._config_columns = [
            'type',
            'description',
            'unit',
            'min_threshold',
            'max_threshold',
            'update_time',
        ]
        self._audio_monitor: Optional[AudioRTSPMonitor] = None
        self._bms_alert_tracker: Dict[str, float] = {}
        self._fetch_limits = {
            'sensors': 60,
            'bms': 60,
            'rfid': 60,
            'cableway': 80,
            'alarms': 80,
            'commands': 50,
            'images': 30,
            'audio': 30,
        }
        self._page_state = {
            'sensors': {'page': 0, 'size': 20, 'has_next': True},
            'bms': {'page': 0, 'size': 20, 'has_next': True},
            'rfid': {'page': 0, 'size': 20, 'has_next': True},
            'alarms': {'page': 0, 'size': 20, 'has_next': True},
        }
        self._time_range_options = [
            ('\u6700\u8fd124\u5c0f\u65f6', 1),
            ('\u6700\u8fd13\u5929', 3),
            ('\u6700\u8fd17\u5929', 7),
            ('\u6700\u8fd130\u5929', 30),
            ('\u5168\u90e8', None),
        ]
        self._time_range_label_to_days = {label: days for label, days in self._time_range_options}
        self._time_range_labels = [label for label, _ in self._time_range_options]
        self._time_range_follow_label = '\u8ddf\u968f\u8bbe\u7f6e'
        self._time_range_default = tk.StringVar(value=self._time_range_labels[0])
        self._time_range_overrides = {
            key: tk.StringVar(value=self._time_range_follow_label)
            for key in self._page_state.keys()
        }
        self._page_labels: Dict[str, ttk.Label] = {}
        self._page_buttons: Dict[str, Dict[str, ttk.Button]] = {}

        self._build_ui()
        self._configure_logging()
        self._start_audio_monitor()
        self._schedule_refresh()

    # -- UI setup -----------------------------------------------------------
    def _build_ui(self) -> None:
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True)

        sensors_frame = ttk.Frame(notebook)
        bms_frame = ttk.Frame(notebook)
        rfid_frame = ttk.Frame(notebook)
        cableway_frame = ttk.Frame(notebook)
        alarms_frame = ttk.Frame(notebook)
        images_frame = ttk.Frame(notebook)
        audio_frame = ttk.Frame(notebook)
        runtime_settings_frame = ttk.Frame(notebook)
        commands_frame = ttk.Frame(notebook)
        playback_frame = ttk.Frame(notebook)
        logs_frame = ttk.Frame(notebook)

        notebook.add(sensors_frame, text='传感器')
        notebook.add(bms_frame, text='电池/BMS')
        notebook.add(rfid_frame, text='RFID')
        notebook.add(cableway_frame, text='索道')
        notebook.add(alarms_frame, text='告警')
        notebook.add(images_frame, text='截图')
        notebook.add(audio_frame, text='音频')
        notebook.add(runtime_settings_frame, text='设置')
        notebook.add(commands_frame, text='命令')
        notebook.add(playback_frame, text='视频预览')
        notebook.add(logs_frame, text='日志')

        self._build_pagination_controls(sensors_frame, 'sensors')
        self.sensor_tree = self._create_treeview(
            sensors_frame,
            [
                ('timestamp', '时间'),
                ('device_id', '设备'),
                ('location', '位置'),
                ('temperature', '温度'),
                ('humidity', '湿度'),
                ('pressure', '压力'),
                ('smoke', '烟雾'),
                ('co', 'CO'),
                ('o2', 'O₂'),
                ('h2s', 'H₂S'),
                ('ch4', 'CH₄'),
            ],
        )

        self._build_pagination_controls(bms_frame, 'bms')
        self.bms_tree = self._create_treeview(
            bms_frame,
            [
                ('timestamp', '时间'),
                ('device_id', '设备'),
                ('location', '位置'),
                ('voltage', '电压 (V)'),
                ('soc', 'SOC (%)'),
                ('status', '状态'),
                ('capacity', '容量 (Ah)'),
                ('power', '功率 (W)'),
                ('current', '电流 (A)'),
                ('cell_voltages', '单体电压'),
            ],
        )

        self._build_pagination_controls(rfid_frame, 'rfid')
        self.rfid_tree = self._create_treeview(
            rfid_frame,
            [
                ('timestamp', '时间'),
                ('device_id', '设备'),
                ('card_id', '卡号'),
                ('length', '长度'),
                ('location', '位置'),
            ],
        )

        self._build_cableway_tab(cableway_frame)
        self._build_pagination_controls(alarms_frame, 'alarms')
        self.alarm_tree = self._create_treeview(
            alarms_frame,
            [
                ('timestamp', '时间'),
                ('sensor_name', '传感器'),
                ('sensor_key', '字段'),
                ('value', '数值'),
                ('unit', '单位'),
                ('alarm_type', '类型'),
                ('location', '位置'),
                ('is_handled', '已处理'),
            ],
        )

        self._build_runtime_settings_tab(runtime_settings_frame)
        self._build_images_tab(images_frame)
        self._build_audio_tab(audio_frame)
        self._build_commands_tab(commands_frame)
        self._build_playback_tab(playback_frame)
        self._build_logs_tab(logs_frame)

        # Status bar
        status_bar = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_var.set('准备就绪')

    def _create_treeview(
        self,
        container: ttk.Frame,
        columns: List[tuple[str, str]],
    ) -> ttk.Treeview:
        tree = ttk.Treeview(container, columns=[col[0] for col in columns], show='headings')
        tree.pack(fill=tk.BOTH, expand=True)
        for field, label in columns:
            tree.heading(field, text=label)
            width = 400 if field == 'cell_voltages' else 140
            tree.column(field, width=width, minwidth=100, anchor=tk.CENTER, stretch=True)
        scrollbar = ttk.Scrollbar(container, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        scrollbar_x = ttk.Scrollbar(container, orient=tk.HORIZONTAL, command=tree.xview)
        tree.configure(xscrollcommand=scrollbar_x.set)
        scrollbar_x.pack(side=tk.BOTTOM, fill=tk.X)
        return tree

    def _get_page_params(self, key: str) -> Dict[str, int]:
        state = self._page_state.get(key)
        if not state:
            return {}
        size = int(state.get('size') or 20)
        page = int(state.get('page') or 0)
        return {'limit': size, 'offset': page * size}

    def _get_page_snapshot(self, keys: List[str]) -> Dict[str, int]:
        snapshot: Dict[str, int] = {}
        for key in keys:
            state = self._page_state.get(key)
            if state is None:
                continue
            snapshot[key] = int(state.get('page') or 0)
        return snapshot

    def _build_page_params(self, key: str) -> Dict[str, Any]:
        params: Dict[str, Any] = self._get_page_params(key) or {
            'limit': self._fetch_limits.get(key, 60)
        }
        start = self._get_time_range_start(key)
        if start:
            params = dict(params)
            params['start'] = start
        return params

    def _get_effective_time_range_label(self, key: str) -> str:
        label = self._time_range_default.get()
        override = self._time_range_overrides.get(key)
        if override:
            label = override.get() or label
        if label == self._time_range_follow_label:
            label = self._time_range_default.get()
        return label

    def _get_time_range_start(self, key: str) -> Optional[str]:
        label = self._get_effective_time_range_label(key)
        days = self._time_range_label_to_days.get(label)
        if days is None:
            # Provide an explicit early timestamp to override the API's default 24h window.
            return '1970-01-01T00:00:00'
        start_at = datetime.now() - timedelta(days=days)
        return start_at.isoformat(timespec='seconds')

    def _on_default_time_range_change(self) -> None:
        for key, var in self._time_range_overrides.items():
            if var.get() == self._time_range_follow_label:
                state = self._page_state.get(key)
                if state is not None:
                    state['page'] = 0
                    self._update_pagination_controls(key)
        self._refresh_data()

    def _on_page_time_range_change(self, key: str) -> None:
        state = self._page_state.get(key)
        if not state:
            return
        state['page'] = 0
        self._update_pagination_controls(key)
        self._refresh_pages([key])

    def _update_page_has_next(self, key: str, row_count: int) -> None:
        state = self._page_state.get(key)
        if not state:
            return
        size = int(state.get('size') or 20)
        state['has_next'] = row_count >= size
        self._update_pagination_controls(key)

    def _update_pagination_controls(self, key: str) -> None:
        state = self._page_state.get(key)
        if not state:
            return
        label = self._page_labels.get(key)
        if label:
            page = int(state.get('page') or 0) + 1
            size = int(state.get('size') or 20)
            label.config(text=f"第 {page} 页 / 每页 {size}")
        buttons = self._page_buttons.get(key, {})
        prev_btn = buttons.get('prev')
        next_btn = buttons.get('next')
        if prev_btn:
            prev_btn.config(state=tk.DISABLED if int(state.get('page') or 0) <= 0 else tk.NORMAL)
        if next_btn:
            has_next = bool(state.get('has_next', True))
            next_btn.config(state=tk.DISABLED if not has_next else tk.NORMAL)

    def _change_page(self, key: str, delta: int) -> None:
        state = self._page_state.get(key)
        if not state:
            return
        current = int(state.get('page') or 0)
        new_page = max(0, current + int(delta))
        if new_page == current:
            return
        state['page'] = new_page
        self._update_pagination_controls(key)
        self._refresh_pages([key])

    def _build_pagination_controls(self, container: ttk.Frame, key: str) -> None:
        frame = ttk.Frame(container)
        frame.pack(fill=tk.X, padx=10, pady=4)
        prev_btn = ttk.Button(
            frame,
            text='上一页',
            command=lambda k=key: self._change_page(k, -1),
        )
        prev_btn.pack(side=tk.LEFT, padx=4)
        label = ttk.Label(frame, text='')
        label.pack(side=tk.LEFT, padx=4)
        next_btn = ttk.Button(
            frame,
            text='下一页',
            command=lambda k=key: self._change_page(k, 1),
        )
        next_btn.pack(side=tk.LEFT, padx=4)
        range_var = self._time_range_overrides.get(key)
        if range_var is None:
            range_var = tk.StringVar(value=self._time_range_follow_label)
            self._time_range_overrides[key] = range_var
        range_label = ttk.Label(frame, text='\u65f6\u95f4\u8303\u56f4\uff1a')
        range_label.pack(side=tk.LEFT, padx=(16, 4))
        range_values = [self._time_range_follow_label] + self._time_range_labels
        range_combo = ttk.Combobox(
            frame,
            textvariable=range_var,
            values=range_values,
            state='readonly',
            width=14,
        )
        range_combo.pack(side=tk.LEFT, padx=4)
        range_combo.bind('<<ComboboxSelected>>', lambda _event, k=key: self._on_page_time_range_change(k))
        self._page_buttons[key] = {'prev': prev_btn, 'next': next_btn}
        self._page_labels[key] = label
        self._update_pagination_controls(key)

    def _build_cableway_tab(self, container: ttk.Frame) -> None:
        control = ttk.LabelFrame(container, text='索道 PLC 一键下发（通过 MQTT 转发到网关执行）')
        control.pack(fill=tk.X, padx=10, pady=10)

        ttk.Label(control, text='设备 ID（可选）：').grid(row=0, column=0, sticky=tk.W, padx=5, pady=4)
        ttk.Entry(control, textvariable=self.cableway_device, width=24).grid(
            row=0, column=1, sticky=tk.W, padx=5, pady=4
        )

        ttk.Label(control, text='请求 ID：').grid(row=0, column=2, sticky=tk.W, padx=5, pady=4)
        ttk.Entry(control, textvariable=self.cableway_request_id, width=24).grid(
            row=0, column=3, sticky=tk.W, padx=5, pady=4
        )

        ttk.Label(control, text='备注（可选）：').grid(row=1, column=0, sticky=tk.W, padx=5, pady=4)
        ttk.Entry(control, textvariable=self.cableway_notes, width=80).grid(
            row=1, column=1, columnspan=3, sticky=tk.W, padx=5, pady=4
        )

        buttons = ttk.Frame(control)
        buttons.grid(row=2, column=0, columnspan=4, sticky=tk.W, padx=5, pady=6)

        def add_button(label: str, fn) -> None:
            ttk.Button(buttons, text=label, command=fn).pack(side=tk.LEFT, padx=4, pady=2)

        add_button('回原点', lambda: self._send_cableway_control(101))
        add_button('自动正向', lambda: self._send_cableway_control(201))
        add_button('自动反向', lambda: self._send_cableway_control(202))
        add_button('定位启动', lambda: self._send_cableway_control(301))
        add_button('点动前进', lambda: self._send_cableway_control(401))
        add_button('点动后退', lambda: self._send_cableway_control(402))
        add_button('停止', lambda: self._send_cableway_control(88))
        add_button('故障复位', lambda: self._send_cableway_control(66))
        add_button('急停', self._send_cableway_estop)

        param_frame = ttk.LabelFrame(container, text='参数设置（VD2000~VD2048，32位浮点）')
        param_frame.pack(fill=tk.X, padx=10, pady=5)

        ttk.Label(param_frame, text='参数键：').grid(row=0, column=0, sticky=tk.W, padx=5, pady=4)
        keys = sorted(FLOAT_PARAMS.keys())
        combo = ttk.Combobox(
            param_frame,
            textvariable=self.cableway_param_key,
            values=keys,
            state='readonly',
            width=28,
        )
        combo.grid(row=0, column=1, sticky=tk.W, padx=5, pady=4)
        combo.bind('<<ComboboxSelected>>', lambda _event: self._on_cableway_param_select())

        ttk.Label(param_frame, textvariable=self.cableway_param_hint).grid(
            row=0, column=2, sticky=tk.W, padx=5, pady=4
        )

        ttk.Label(param_frame, text='数值：').grid(row=1, column=0, sticky=tk.W, padx=5, pady=4)
        ttk.Entry(param_frame, textvariable=self.cableway_param_value, width=16).grid(
            row=1, column=1, sticky=tk.W, padx=5, pady=4
        )
        ttk.Button(param_frame, text='写入参数', command=self._send_cableway_param).grid(
            row=1, column=2, sticky=tk.W, padx=5, pady=4
        )

        list_frame = ttk.LabelFrame(container, text='索道状态（最近记录）')
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.cableway_tree = self._create_treeview(
            list_frame,
            [
                ('timestamp', '时间'),
                ('device_id', '设备'),
                ('location', '位置'),
                ('plc_host', 'PLC'),
                ('active_faults', '故障(Active)'),
                ('heartbeat_timeout', '心跳超时'),
                ('q_fault', 'Q故障'),
                ('q_forward', '正转'),
                ('q_reverse', '反转'),
                ('command_in_progress', '命令执行中'),
            ],
        )
        if self.cableway_tree:
            self.cableway_tree.column('active_faults', width=520, minwidth=220, anchor=tk.W, stretch=True)
            self.cableway_tree.bind('<<TreeviewSelect>>', lambda _event: self._on_cableway_selected())

        detail = ttk.LabelFrame(container, text='详情（JSON）')
        detail.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.cableway_detail_text = scrolledtext.ScrolledText(detail, height=8, state=tk.DISABLED)
        self.cableway_detail_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def _build_commands_tab(self, container: ttk.Frame) -> None:
        form = ttk.Frame(container)
        form.pack(fill=tk.X, padx=10, pady=10)

        ttk.Label(form, text='指令载荷 (十六进制/列表)：').grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        entry_payload = ttk.Entry(form, textvariable=self.command_payload, width=80)
        entry_payload.grid(row=0, column=1, sticky=tk.W, padx=5, pady=5)

        ttk.Label(form, text='请求 ID（可选）：').grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        ttk.Entry(form, textvariable=self.command_request_id, width=40).grid(row=1, column=1, sticky=tk.W, padx=5, pady=5)

        ttk.Label(form, text='设备 ID（可选）：').grid(row=2, column=0, sticky=tk.W, padx=5, pady=5)
        ttk.Entry(form, textvariable=self.command_device, width=30).grid(row=2, column=1, sticky=tk.W, padx=5, pady=5)

        ttk.Label(form, text='备注（可选）：').grid(row=3, column=0, sticky=tk.W, padx=5, pady=5)
        ttk.Entry(form, textvariable=self.command_notes, width=80).grid(row=3, column=1, sticky=tk.W, padx=5, pady=5)

        buttons = ttk.Frame(container)
        buttons.pack(fill=tk.X, padx=10, pady=5)

        ttk.Button(buttons, text='发送命令', command=self.send_command).pack(side=tk.LEFT, padx=5)
        ttk.Button(buttons, text='清空', command=self._clear_command_inputs).pack(side=tk.LEFT, padx=5)

        history_label = ttk.Label(container, text='命令历史')
        history_label.pack(anchor=tk.W, padx=10)

        self.command_history = scrolledtext.ScrolledText(container, height=12, state=tk.DISABLED)
        self.command_history.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

    def _build_logs_tab(self, container: ttk.Frame) -> None:
        self.log_text = scrolledtext.ScrolledText(container, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    def _build_config_tab(self, container: ttk.Frame) -> None:
        self.config_tree = self._create_treeview(
            container,
            [
                ('type', '类型'),
                ('description', '描述'),
                ('unit', '单位'),
                ('min_threshold', '最小阈值'),
                ('max_threshold', '最大阈值'),
                ('update_time', '更新时间'),
            ],
        )
        if self.config_tree:
            self.config_tree.bind('<<TreeviewSelect>>', lambda _event: self._on_config_select())

        form = ttk.LabelFrame(container, text='阈值编辑')
        form.pack(fill=tk.X, padx=10, pady=10)

        self.config_form_vars = {
            'type': tk.StringVar(),
            'description': tk.StringVar(),
            'unit': tk.StringVar(),
            'min_threshold': tk.StringVar(),
            'max_threshold': tk.StringVar(),
            'version': tk.StringVar(value='0'),
        }

        fields = [
            ('type', '传感器类型'),
            ('description', '描述'),
            ('unit', '单位'),
            ('min_threshold', '最小阈值'),
            ('max_threshold', '最大阈值'),
            ('version', '版本'),
        ]
        for row, (field, label) in enumerate(fields):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky=tk.W, padx=5, pady=3)
            if field == 'type' and self._sensor_type_options:
                combo_values = [''] + self._sensor_type_options
                combo = ttk.Combobox(
                    form,
                    textvariable=self.config_form_vars[field],
                    values=combo_values,
                    state='readonly',
                )
                combo.grid(row=row, column=1, sticky=tk.EW, padx=5, pady=3)
                self._sensor_type_combo = combo
                combo.bind('<<ComboboxSelected>>', lambda _event: self._on_config_type_change())
            else:
                entry = ttk.Entry(form, textvariable=self.config_form_vars[field])
                entry.grid(row=row, column=1, sticky=tk.EW, padx=5, pady=3)
        form.columnconfigure(1, weight=1)

        buttons = ttk.Frame(form)
        buttons.grid(row=len(fields), column=0, columnspan=2, sticky=tk.E, pady=8)
        ttk.Button(buttons, text='保存/更新', command=self._save_config).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text='删除', command=self._delete_config).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text='清空', command=self._clear_config_form).pack(side=tk.LEFT, padx=4)

    def _build_runtime_settings_tab(self, container: ttk.Frame) -> None:
        wrapper = ttk.Frame(container)
        wrapper.pack(fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(wrapper, highlightthickness=0)
        scrollbar = ttk.Scrollbar(wrapper, orient=tk.VERTICAL, command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)

        def _on_configure(_event: tk.Event) -> None:
            canvas.configure(scrollregion=canvas.bbox('all'))

        scroll_frame.bind('<Configure>', _on_configure)
        canvas.create_window((0, 0), window=scroll_frame, anchor='nw')
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        time_range_frame = ttk.LabelFrame(scroll_frame, text='\u9ed8\u8ba4\u65f6\u95f4\u8303\u56f4')
        time_range_frame.pack(fill=tk.X, padx=10, pady=8)
        ttk.Label(time_range_frame, text='\u6570\u636e\u67e5\u8be2\u9ed8\u8ba4\u8303\u56f4\uff1a').grid(
            row=0, column=0, sticky=tk.W, padx=5, pady=5
        )
        default_combo = ttk.Combobox(
            time_range_frame,
            textvariable=self._time_range_default,
            values=self._time_range_labels,
            state='readonly',
            width=18,
        )
        default_combo.grid(row=0, column=1, sticky=tk.W, padx=5, pady=5)
        default_combo.bind('<<ComboboxSelected>>', lambda _event: self._on_default_time_range_change())
        ttk.Label(time_range_frame, text='\u4ec5\u5f71\u54cd\u201c\u8ddf\u968f\u8bbe\u7f6e\u201d\u7684\u9875\u9762').grid(
            row=0, column=2, sticky=tk.W, padx=5, pady=5
        )
        time_range_frame.columnconfigure(2, weight=1)

        self.runtime_config_vars = {}
        self._runtime_option_meta = {}
        self._runtime_options_container = ttk.Frame(scroll_frame)
        self._runtime_options_container.pack(fill=tk.BOTH, expand=True)

        threshold_frame = ttk.LabelFrame(scroll_frame, text='\u9608\u503c\u914d\u7f6e')
        threshold_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self._build_config_tab(threshold_frame)

        self._load_runtime_history()
        self._refresh_runtime_options()
        actions = ttk.Frame(container)
        actions.pack(fill=tk.X, padx=10, pady=8)

        refresh_options_btn = ttk.Button(
            actions,
            text='\u5237\u65b0\u914d\u7f6e\u9879',
            command=self._refresh_runtime_options_async,
        )
        refresh_options_btn.pack(side=tk.LEFT, padx=4)
        refresh_options_status = ttk.Label(actions, text='')
        refresh_options_status.pack(side=tk.LEFT, padx=4)

        refresh_config_btn = ttk.Button(
            actions,
            text='\u5237\u65b0\u914d\u7f6e',
            command=self._load_runtime_config_async,
        )
        refresh_config_btn.pack(side=tk.LEFT, padx=4)
        refresh_config_status = ttk.Label(actions, text='')
        refresh_config_status.pack(side=tk.LEFT, padx=4)

        apply_btn = ttk.Button(actions, text='\u5e94\u7528\u8986\u76d6', command=self._save_runtime_config)
        apply_btn.pack(side=tk.LEFT, padx=4)

        self._runtime_action_buttons['options'] = refresh_options_btn
        self._runtime_action_buttons['config'] = refresh_config_btn
        self._runtime_action_labels['options'] = refresh_options_status
        self._runtime_action_labels['config'] = refresh_config_status


    def _load_runtime_history(self) -> None:
        try:
            raw = self._runtime_history_path.read_text(encoding='utf-8')
        except OSError:
            self._runtime_history = {}
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            self._runtime_history = {}
            return
        if not isinstance(data, dict):
            self._runtime_history = {}
            return
        items = data.get('items') if 'items' in data else data
        if not isinstance(items, dict):
            self._runtime_history = {}
            return
        history: Dict[str, List[str]] = {}
        for key, values in items.items():
            if not isinstance(key, str):
                continue
            if not isinstance(values, list):
                continue
            cleaned: List[str] = []
            for value in values:
                text_val = '' if value is None else str(value).strip()
                if not text_val or text_val in cleaned:
                    continue
                cleaned.append(text_val)
            if cleaned:
                history[key] = cleaned[:20]
        self._runtime_history = history

    def _save_runtime_history(self) -> None:
        self._runtime_history_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {'version': 1, 'items': self._runtime_history}
        self._runtime_history_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )

    def _get_runtime_history_values(self, key: str) -> List[str]:
        values = self._runtime_history.get(key) or []
        return [value for value in values if isinstance(value, str)]

    def _record_runtime_history(self, key: str, value: object) -> None:
        text_val = '' if value is None else str(value).strip()
        if not text_val:
            return
        history = self._runtime_history.get(key, [])
        history = [item for item in history if item != text_val]
        history.insert(0, text_val)
        self._runtime_history[key] = history[:20]
        widget = self._runtime_option_widgets.get(key)
        if isinstance(widget, ttk.Combobox):
            widget['values'] = self._runtime_history[key]
        self._save_runtime_history()

    def _set_runtime_action_state(self, action: str, *, busy: bool, message: str = '') -> None:
        label = self._runtime_action_labels.get(action)
        if label:
            label.config(text=message)
        if busy:
            self._runtime_action_busy += 1
        else:
            self._runtime_action_busy = max(0, self._runtime_action_busy - 1)
        is_busy = self._runtime_action_busy > 0
        for btn in self._runtime_action_buttons.values():
            if btn:
                btn.config(state=tk.DISABLED if is_busy else tk.NORMAL)
        if label and message in {'\u5b8c\u6210', '\u5931\u8d25'}:
            def _clear_label() -> None:
                if label.cget('text') == message:
                    label.config(text='')
            self.root.after(1500, _clear_label)

    def _hash_runtime_sections(self, sections: List[Dict[str, Any]]) -> str:
        try:
            payload = json.dumps(sections, ensure_ascii=False, sort_keys=True)
        except TypeError:
            payload = str(sections)
        return hashlib.md5(payload.encode('utf-8')).hexdigest()

    def _apply_runtime_sections(self, sections: List[Dict[str, Any]], *, force: bool = False) -> bool:
        normalized = self._normalize_runtime_sections(sections)
        new_hash = self._hash_runtime_sections(normalized)
        if not force and new_hash == self._runtime_options_hash:
            return False
        for child in self._runtime_options_container.winfo_children():
            child.destroy()
        self.runtime_config_vars = {}
        self._runtime_option_meta = {}
        self._runtime_option_widgets = {}
        self._render_runtime_config_sections(self._runtime_options_container, normalized)
        self._runtime_options_hash = new_hash
        return True

    def _fetch_runtime_config_payload(self) -> Dict[str, Any]:
        url = f'{self.base_url}/api/runtime-config'
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    def _apply_runtime_config_values(self, config: Dict[str, Any]) -> None:
        for key, var in self.runtime_config_vars.items():
            if key not in config:
                continue
            value = config.get(key)
            meta = self._runtime_option_meta.get(key, {})
            opt_type = meta.get('type')
            if opt_type == 'bool':
                var.set(self._coerce_bool_value(value))
            elif opt_type == 'list':
                if isinstance(value, list):
                    var.set(','.join(str(item) for item in value))
                else:
                    var.set('' if value is None else str(value))
            else:
                var.set('' if value is None else str(value))

    def _apply_runtime_config_payload(self, payload: Dict[str, Any]) -> None:
        config = payload.get('config') or {}
        if not isinstance(config, dict):
            return
        missing_keys = [key for key in config.keys() if key not in self.runtime_config_vars]
        if missing_keys:
            self._refresh_runtime_options(force=True)
        self._apply_runtime_config_values(config)

    def _refresh_runtime_options_async(self) -> None:
        if self._runtime_action_busy > 0:
            return
        self._set_runtime_action_state('options', busy=True, message='\u52a0\u8f7d\u4e2d...')
        threading.Thread(target=self._refresh_runtime_options_thread, daemon=True).start()

    def _refresh_runtime_options_thread(self) -> None:
        try:
            sections = self._fetch_runtime_config_sections()
            payload = self._fetch_runtime_config_payload()
        except Exception as exc:
            self.root.after(0, lambda e=exc: messagebox.showerror('\u914d\u7f6e\u52a0\u8f7d\u5931\u8d25', f'{e}'))
            self.root.after(0, lambda: self._set_runtime_action_state('options', busy=False, message='\u5931\u8d25'))
            return

        def _apply() -> None:
            self._apply_runtime_sections(sections)
            self._apply_runtime_config_payload(payload)
            self._set_runtime_action_state('options', busy=False, message='\u5b8c\u6210')

        self.root.after(0, _apply)

    def _load_runtime_config_async(self) -> None:
        if self._runtime_action_busy > 0:
            return
        self._set_runtime_action_state('config', busy=True, message='\u52a0\u8f7d\u4e2d...')
        threading.Thread(target=self._load_runtime_config_thread, daemon=True).start()

    def _load_runtime_config_thread(self) -> None:
        try:
            payload = self._fetch_runtime_config_payload()
        except Exception as exc:
            self.root.after(0, lambda e=exc: messagebox.showerror('\u914d\u7f6e\u52a0\u8f7d\u5931\u8d25', f'{e}'))
            self.root.after(0, lambda: self._set_runtime_action_state('config', busy=False, message='\u5931\u8d25'))
            return

        def _apply() -> None:
            self._apply_runtime_config_payload(payload)
            self._set_runtime_action_state('config', busy=False, message='\u5b8c\u6210')

        self.root.after(0, _apply)

    def _coerce_bool_value(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        text = str(value or '').strip().lower()
        return text in {'true', '1', 'yes', 'on'}

    def _fallback_runtime_sections(self) -> List[Dict[str, Any]]:
        sections: List[Dict[str, Any]] = []
        for key, section in CONFIG_SECTIONS.items():
            item = dict(section)
            item.setdefault('key', key)
            sections.append(item)
        return sections

    def _normalize_runtime_sections(self, sections: Any) -> List[Dict[str, Any]]:
        if isinstance(sections, dict):
            normalized: List[Dict[str, Any]] = []
            for key, section in sections.items():
                item = dict(section or {})
                item.setdefault('key', key)
                normalized.append(item)
            return normalized
        if isinstance(sections, list):
            normalized = []
            for section in sections:
                if not isinstance(section, dict):
                    continue
                item = dict(section)
                item.setdefault('key', str(item.get('key') or ''))
                normalized.append(item)
            return normalized
        return self._fallback_runtime_sections()

    def _fetch_runtime_config_sections(self) -> List[Dict[str, Any]]:
        url = f'{self.base_url}/api/runtime-config/options'
        try:
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
        except Exception:
            return self._fallback_runtime_sections()
        payload = resp.json() if resp.content else {}
        sections = payload.get('sections') or []
        return self._normalize_runtime_sections(sections)

    def _render_runtime_config_sections(self, container: ttk.Frame, sections: List[Dict[str, Any]]) -> None:
        for section in sections:
            section_key = str(section.get('key') or "")
            title = str(section.get('title') or section_key)
            section_frame = ttk.LabelFrame(
                container,
                text=title or section_key,
            )
            section_frame.pack(fill=tk.X, padx=10, pady=8)

            description = str(section.get('description') or "").strip()
            if description:
                ttk.Label(section_frame, text=description).grid(
                    row=0,
                    column=0,
                    columnspan=3,
                    sticky=tk.W,
                    padx=6,
                    pady=(4, 8),
                )

            options = section.get('options') or []
            for index, option in enumerate(options, start=1):
                key = str(option.get('key') or "")
                if not key:
                    continue
                opt_type = str(option.get('type') or "text")
                default_value = option.get('default', "")
                label = str(option.get('label') or key)
                hint = str(option.get('hint') or "")
                effect = str(option.get('effect') or "")
                hint_text = f'{hint} ({effect})' if hint or effect else ''

                ttk.Label(section_frame, text=label).grid(
                    row=index,
                    column=0,
                    sticky=tk.W,
                    padx=6,
                    pady=4,
                )

                if opt_type == 'bool':
                    var: tk.Variable = tk.BooleanVar(value=self._coerce_bool_value(default_value))
                    widget = ttk.Checkbutton(section_frame, variable=var)
                else:
                    default_text = ""
                    if opt_type == 'list' and isinstance(default_value, list):
                        default_text = ','.join(str(item) for item in default_value)
                    else:
                        default_text = "" if default_value is None else str(default_value)
                    if opt_type == 'select':
                        values = [str(item) for item in (option.get('choices') or [])]
                        if values and default_text not in values:
                            default_text = values[0]
                        var = tk.StringVar(value=default_text)
                        widget = ttk.Combobox(
                            section_frame,
                            textvariable=var,
                            values=values,
                            state='readonly',
                        )
                    else:
                        var = tk.StringVar(value=default_text)
                        history_values = self._get_runtime_history_values(key)
                        widget = ttk.Combobox(
                            section_frame,
                            textvariable=var,
                            values=history_values,
                            state='normal',
                        )
                widget.grid(row=index, column=1, sticky=tk.EW, padx=6, pady=4)
                section_frame.columnconfigure(1, weight=1)

                if hint_text:
                    ttk.Label(section_frame, text=hint_text).grid(
                        row=index,
                        column=2,
                        sticky=tk.W,
                        padx=6,
                        pady=4,
                    )

                self.runtime_config_vars[key] = var
                self._runtime_option_meta[key] = {
                    'type': opt_type,
                    'select': opt_type == 'select',
                }
                self._runtime_option_widgets[key] = widget

    def _refresh_runtime_options(self, force: bool = False) -> None:
        sections = self._fetch_runtime_config_sections()
        self._apply_runtime_sections(sections, force=force)

    def _load_runtime_config(self) -> None:
        try:
            payload = self._fetch_runtime_config_payload()
        except Exception as exc:
            messagebox.showerror('\u914d\u7f6e\u52a0\u8f7d\u5931\u8d25', f'{exc}')
            return
        self._apply_runtime_config_payload(payload)

    def _save_runtime_config(self) -> None:
        overrides: Dict[str, Any] = {}
        for key, var in self.runtime_config_vars.items():
            meta = self._runtime_option_meta.get(key, {})
            opt_type = meta.get('type')
            value = var.get()
            if opt_type == 'bool':
                overrides[key] = self._coerce_bool_value(value)
                continue
            try:
                if opt_type == 'int':
                    text = str(value).strip()
                    if text == '':
                        messagebox.showerror('配置输入错误', f'{key} 需要整数')
                        return
                    overrides[key] = int(text)
                elif opt_type == 'float':
                    text = str(value).strip()
                    if text == '':
                        messagebox.showerror('配置输入错误', f'{key} 需要数值')
                        return
                    overrides[key] = float(text)
                elif opt_type == 'list':
                    text = str(value).strip()
                    overrides[key] = [item.strip() for item in text.split(',') if item.strip()] if text else []
                else:
                    overrides[key] = '' if value is None else value
            except ValueError:
                messagebox.showerror('配置输入错误', f'{key} 的值无效')
                return
        url = f'{self.base_url}/api/runtime-config'
        try:
            resp = requests.patch(url, json=overrides, timeout=5)
            resp.raise_for_status()
        except Exception as exc:
            messagebox.showerror('配置保存失败', f'{exc}')
            return
        payload = resp.json() if resp.content else {}
        for key, value in overrides.items():
            meta = self._runtime_option_meta.get(key, {})
            opt_type = meta.get('type')
            if opt_type in {'int', 'float', 'text', 'list'} and not meta.get('select'):
                if opt_type == 'list' and isinstance(value, list):
                    history_value = ','.join(str(item) for item in value)
                else:
                    history_value = value
                self._record_runtime_history(key, history_value)
        restart_scheduled = bool(payload.get('restart_scheduled'))
        if restart_scheduled:
            messagebox.showinfo('配置更新', '配置已提交，API 将自动重启，请稍后刷新。')
        else:
            messagebox.showinfo('配置更新', '运行期配置已提交。')
    def _build_images_tab(self, container: ttk.Frame) -> None:
        wrapper = ttk.Frame(container)
        wrapper.pack(fill=tk.BOTH, expand=True)

        tree_frame = ttk.Frame(wrapper)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        columns = ('timestamp', 'device_id', 'location', 'image_name')
        tree = ttk.Treeview(tree_frame, columns=columns, show='headings', height=8)
        headings = [
            ('timestamp', '时间'),
            ('device_id', '设备'),
            ('location', '位置'),
            ('image_name', '文件名'),
        ]
        for column, label in headings:
            tree.heading(column, text=label)
            tree.column(column, width=150, anchor=tk.W)
        tree.column('timestamp', width=180)

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        tree.bind('<<TreeviewSelect>>', lambda _event: self._on_image_selected())
        self.images_tree = tree

        preview_frame = ttk.LabelFrame(wrapper, text='预览')
        preview_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        label = ttk.Label(preview_frame, text='暂无截图', anchor=tk.CENTER, justify=tk.CENTER)
        label.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.image_preview_label = label

    def _build_playback_tab(self, container: ttk.Frame) -> None:
        form = ttk.LabelFrame(container, text='RTSP Playback (YOLO annotated)')
        form.pack(fill=tk.X, padx=10, pady=10)

        default_url = (
            (self.settings.yolo_rtsp_output or '').strip()
            or (self.settings.yolo_rtsp_input or '').strip()
            or ''
        )
        self.playback_url.set(default_url)

        ttk.Label(form, text='RTSP URL:').grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        entry = ttk.Entry(form, textvariable=self.playback_url, width=80)
        entry.grid(row=0, column=1, sticky=tk.EW, padx=5, pady=5)
        form.columnconfigure(1, weight=1)

        btns = ttk.Frame(form)
        btns.grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=5)
        ttk.Button(btns, text='内嵌播放', command=self._start_playback).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text='内嵌停止', command=self._stop_playback).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text='外部窗口播放', command=self._start_playback_external).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text='外部停止', command=self._stop_playback_external).pack(side=tk.LEFT, padx=4)
        ttk.Label(btns, text='需本机已安装 ffmpeg/ffplay').pack(side=tk.LEFT, padx=8)

        video_frame = ttk.LabelFrame(container, text='预览')
        video_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.playback_canvas = tk.Canvas(video_frame, bg='black', height=360)
        self.playback_canvas.pack(fill=tk.BOTH, expand=True)
        self.playback_photo: Optional[ImageTk.PhotoImage] = None
        self.playback_timer_id: Optional[str] = None

    def _build_audio_tab(self, container: ttk.Frame) -> None:
        pane = ttk.Panedwindow(container, orient=tk.VERTICAL)
        pane.pack(fill=tk.BOTH, expand=True)

        upper = ttk.Frame(pane)
        lower = ttk.Frame(pane)
        pane.add(upper, weight=2)
        pane.add(lower, weight=1)

        form = ttk.LabelFrame(upper, text='音频阈值设置')
        form.pack(fill=tk.X, padx=10, pady=10)

        thresholds = {
            'centroid': self.settings.audio_threshold_centroid,
            'bandwidth': self.settings.audio_threshold_bandwidth,
            'rolloff': self.settings.audio_threshold_rolloff,
            'flatness': self.settings.audio_threshold_flatness,
            'flux': self.settings.audio_threshold_flux,
            'rms': getattr(self.settings, 'audio_threshold_rms', 0.0),
        }
        labels = {
            'centroid': '谱质心',
            'bandwidth': '谱带宽度',
            'rolloff': '谱滚降',
            'flatness': '谱平坦度',
            'flux': '谱通量',
            'rms': 'RMS',
        }
        for idx, (key, label_text) in enumerate(labels.items()):
            ttk.Label(form, text=f'{label_text}阈值').grid(row=idx, column=0, sticky=tk.W, padx=5, pady=3)
            var = tk.StringVar(value=str(thresholds[key]))
            self.audio_threshold_vars[key] = var
            entry = ttk.Entry(form, textvariable=var, width=12)
            entry.grid(row=idx, column=1, sticky=tk.W, padx=5, pady=3)

        ttk.Button(form, text='保存阈值', command=self._save_audio_thresholds).grid(
            row=len(labels), column=0, columnspan=2, sticky=tk.W, padx=5, pady=5
        )

        chart_frame = ttk.LabelFrame(upper, text='音频特征趋势')
        chart_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        self.audio_fig = Figure(figsize=(6, 4), dpi=100)
        ax = self.audio_fig.add_subplot(111)
        self.audio_canvas = FigureCanvasTkAgg(self.audio_fig, master=chart_frame)
        self.audio_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.audio_lines = {
            'centroid': ax.plot([], [], label='谱质心')[0],
            'bandwidth': ax.plot([], [], label='谱带宽度')[0],
            'rolloff': ax.plot([], [], label='谱滚降')[0],
            'flatness': ax.plot([], [], label='谱平坦度')[0],
            'flux': ax.plot([], [], label='谱通量')[0],
        }
        ax.legend(loc='upper right')
        ax.set_xlabel('最近窗口序号（越右边越新）')
        ax.set_ylabel('特征数值（单位随特征）')
        self.audio_fig.tight_layout()

        list_frame = ttk.LabelFrame(lower, text='已保存音频片段')
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        self.audio_tree = self._create_treeview(
            list_frame,
            [
                ('timestamp', '时间'),
                ('device_id', '设备'),
                ('location', '位置'),
                ('audio_name', '文件名'),
                ('size_kb', '大小(KB)'),
            ],
        )
        btns = ttk.Frame(list_frame)
        btns.pack(fill=tk.X, padx=5, pady=5)
        ttk.Button(btns, text='播放选中', command=self._play_selected_audio).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text='停止播放', command=self._stop_audio_playback).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text='保存选中为文件', command=self._save_selected_audio).pack(side=tk.LEFT, padx=4)

    def _build_base_url(self) -> str:
        host = self.settings.api_host or 'localhost'
        if host in ('0.0.0.0', '::'):
            host = 'localhost'
        return f"http://{host}:{self.settings.api_port}"

    # -- Data refresh -------------------------------------------------------
    def _schedule_refresh(self) -> None:
        self.root.after(self.REFRESH_INTERVAL_MS, self._refresh_all)

    def _refresh_all(self) -> None:
        threading.Thread(target=self._refresh_data_thread, daemon=True).start()
        self._schedule_refresh()

    def _refresh_data(self) -> None:
        """立即刷新一次数据（供按钮回调使用）。"""
        threading.Thread(target=self._refresh_data_thread, daemon=True).start()

    def _refresh_pages(self, keys: List[str]) -> None:
        targets = [key for key in keys if key in self._page_state]
        if not targets:
            return
        threading.Thread(
            target=self._refresh_pages_thread,
            args=(targets,),
            daemon=True,
        ).start()

    def _refresh_pages_thread(self, keys: List[str]) -> None:
        try:
            payload: Dict[str, Any] = {}
            payload['_page_snapshot'] = self._get_page_snapshot(keys)
            if 'sensors' in keys:
                payload['sensors'] = self._fetch_json(
                    '/api/sensors',
                    params=self._build_page_params('sensors'),
                )
            if 'bms' in keys:
                payload['bms'] = self._fetch_json(
                    '/api/bms',
                    params=self._build_page_params('bms'),
                )
                payload['bms_alerts'] = self._check_bms_cell_voltages(payload['bms'])
            if 'rfid' in keys:
                payload['rfid'] = self._fetch_json(
                    '/api/rfid',
                    params=self._build_page_params('rfid'),
                )
            if 'alarms' in keys:
                payload['alarms'] = self._fetch_json(
                    '/api/alarms',
                    params=self._build_page_params('alarms'),
                )
            self.root.after(0, lambda data=payload: self._apply_refresh(data))
        except Exception as exc:
            logger.exception('Failed to refresh partial data: %s', exc)
            self.root.after(0, lambda e=exc: self._handle_refresh_error(e))

    def _refresh_data_thread(self) -> None:
        try:
            payload_snapshot = self._get_page_snapshot(['sensors', 'bms', 'rfid', 'alarms'])
            sensor_params = self._build_page_params('sensors')
            bms_params = self._build_page_params('bms')
            rfid_params = self._build_page_params('rfid')
            alarm_params = self._build_page_params('alarms')
            payload = {
                'sensors': self._fetch_json('/api/sensors', params=sensor_params),
                'bms': self._fetch_json('/api/bms', params=bms_params),
                'rfid': self._fetch_json('/api/rfid', params=rfid_params),
                'cableway': self._fetch_json('/api/cableway/status', params={'limit': self._fetch_limits['cableway']}),
                'commands': self._fetch_json('/api/commands', params={'limit': self._fetch_limits['commands']}),
                'configs': self._fetch_json('/api/config/sensors'),
                'alarms': self._fetch_json('/api/alarms', params=alarm_params),
                'images': self._fetch_json('/api/images', params={'limit': self._fetch_limits['images']}),
                'audio': self._fetch_json('/api/audio', params={'limit': self._fetch_limits['audio']}),
                'audio_metrics': self._audio_monitor.get_latest() if self._audio_monitor else None,
            }
            payload['_page_snapshot'] = payload_snapshot
            payload['bms_alerts'] = self._check_bms_cell_voltages(payload['bms'])
            self.root.after(0, lambda data=payload: self._apply_refresh(data))
        except Exception as exc:
            logger.exception('Failed to refresh data: %s', exc)
            self.root.after(0, lambda e=exc: self._handle_refresh_error(e))
    def _apply_refresh(self, data: Dict[str, Any]) -> None:
        try:
            page_snapshot = data.get('_page_snapshot') or {}
            def _is_page_current(key: str) -> bool:
                if key not in page_snapshot:
                    return True
                state = self._page_state.get(key)
                if state is None:
                    return False
                return int(state.get('page') or 0) == int(page_snapshot.get(key, -1))

            if 'sensors' in data and _is_page_current('sensors'):
                sensor_rows = data.get('sensors', [])
                self._update_tree(self.sensor_tree, sensor_rows, [
                    'timestamp', 'device_id', 'location',
                    'temperature', 'humidity', 'pressure',
                    'smoke', 'co', 'o2', 'h2s', 'ch4',
                ])
                self._update_page_has_next('sensors', len(sensor_rows))

            if 'bms' in data and _is_page_current('bms'):
                bms_rows = data.get('bms', [])
                self._update_tree(self.bms_tree, bms_rows, [
                    'timestamp', 'device_id', 'location',
                    'voltage', 'soc', 'status', 'capacity', 'power', 'current', 'cell_voltages',
                ])
                self._update_page_has_next('bms', len(bms_rows))

            if 'rfid' in data and _is_page_current('rfid'):
                rfid_rows = data.get('rfid', [])
                self._update_tree(self.rfid_tree, rfid_rows, [
                    'timestamp', 'device_id', 'card_id', 'length', 'location',
                ])
                self._update_page_has_next('rfid', len(rfid_rows))

            if 'cableway' in data:
                self._update_cableway_tab(data.get('cableway', []))

            if 'commands' in data:
                self._update_command_history(data.get('commands', []))
            if 'configs' in data:
                self._update_config_tree(data.get('configs', []))

            if 'alarms' in data and _is_page_current('alarms'):
                alarm_rows = data.get('alarms', [])
                self._update_tree(
                    self.alarm_tree,
                    alarm_rows,
                    [
                        'timestamp',
                        'sensor_name',
                        'sensor_key',
                        'value',
                        'unit',
                        'alarm_type',
                        'location',
                        'is_handled',
                    ],
                )
                self._update_page_has_next('alarms', len(alarm_rows))

            if 'images' in data:
                self._update_images_tab(data.get('images', []))
            if 'audio' in data:
                self._update_audio_table(data.get('audio', []))

            if 'audio_metrics' in data:
                metrics_data = data.get('audio_metrics')
                if metrics_data:
                    self._audio_metrics_buffer.append(metrics_data)
                    if len(self._audio_metrics_buffer) > 200:
                        self._audio_metrics_buffer = self._audio_metrics_buffer[-200:]
                    self._update_audio_chart()

            self._append_log('\u6570\u636e\u5237\u65b0\u5b8c\u6210')
            self._update_status('\u6570\u636e\u5237\u65b0\u6210\u529f')
            alerts = data.get('bms_alerts') or []
            if alerts:
                self._handle_bms_alerts(alerts)
        except Exception as exc:
            logger.exception('Failed to apply refresh data: %s', exc)
            self._append_log(f'\u6570\u636e\u5237\u65b0\u5931\u8d25: {exc}')
            self._update_status('\u5237\u65b0\u5931\u8d25\uff0c\u8bf7\u68c0\u67e5\u65e5\u5fd7')

    def _handle_refresh_error(self, exc: Exception) -> None:
        self._append_log(f'数据刷新失败: {exc}')
        self._update_status('刷新失败，请检查日志')

    def _start_audio_monitor(self) -> None:
        if not (self.settings.audio_enabled and self.settings.audio_rtsp_input):
            return
        self._audio_monitor = AudioRTSPMonitor(
            url=self.settings.audio_rtsp_input,
            sample_rate=int(self.settings.audio_sample_rate or 16000),
            window_seconds=float(self.settings.audio_window_seconds or 1.0),
        )
        self._audio_monitor.start()

    def _fetch_json(self, path: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        url = self.base_url + path
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        if not response.content or not response.content.strip():
            logger.debug('Empty response from %s', url)
            return []
        try:
            data = response.json()
        except ValueError as exc:
            snippet = response.text.strip().replace('\n', ' ')[:200]
            logger.error('Invalid JSON from %s: %s', url, snippet)
            raise RuntimeError(f'Invalid JSON returned by {url}') from exc
        if isinstance(data, list):
            return data
        return []

    def _update_tree(
        self,
        tree: Optional[ttk.Treeview],
        rows: List[Dict[str, Any]],
        fields: List[str],
    ) -> None:
        if tree is None:
            return
        # Preserve current selection and scroll position to avoid disrupting the user view.
        selected_values = [tree.item(item, 'values') for item in tree.selection()]
        yview = tree.yview()
        self._safe_clear_tree(tree)
        for row in rows:
            values = [self._format_cell(row.get(field)) for field in fields]
            iid = tree.insert('', tk.END, values=values)
            if values in selected_values:
                tree.selection_add(iid)
        if yview:
            tree.yview_moveto(yview[0])

    def _update_command_history(self, rows: List[Dict[str, Any]]) -> None:
        if not self.command_history:
            return
        self.command_history.configure(state=tk.NORMAL)
        self.command_history.delete('1.0', tk.END)
        for row in rows:
            timestamp = row.get('timestamp')
            direction = (row.get('direction') or '').upper()
            payload = row.get('payload')
            notes = row.get('notes') or ''
            device_id = row.get('device_id') or ''
            line = f"[{timestamp}] {direction} payload={payload}"
            if device_id:
                line += f" device={device_id}"
            if notes:
                line += f" notes={notes}"
            request_id = self._extract_request_id(notes)
            if request_id:
                line += f" request_id={request_id}"
            self.command_history.insert(tk.END, line + '\n')
        self.command_history.configure(state=tk.DISABLED)

    def _update_config_tree(self, rows: List[Dict[str, Any]]) -> None:
        if not self.config_tree:
            return
        self._config_cache = {row.get('type'): row for row in rows if row.get('type')}
        self._safe_clear_tree(self.config_tree)
        for row in rows:
            values = [self._format_cell(row.get(field)) for field in getattr(self, '_config_columns', [])]
            self.config_tree.insert('', tk.END, values=values)

    def _update_images_tab(self, rows: List[Dict[str, Any]]) -> None:
        self._image_records = rows or []
        tree = self.images_tree
        if not tree:
            return
        self._image_cache = {}
        self._safe_clear_tree(tree)
        for idx, row in enumerate(self._image_records):
            iid = str(row.get('id') or idx)
            self._image_cache[iid] = row
            values = [
                self._format_cell(row.get('timestamp')),
                self._format_cell(row.get('device_id')),
                self._format_cell(row.get('location')),
                self._format_cell(row.get('image_name')),
            ]
            try:
                tree.insert('', tk.END, iid=iid, values=values)
            except tk.TclError:
                # 再次尝试使用随机 iid 避免重复
                tree.insert('', tk.END, values=values)
        if not self._image_records:
            self._clear_image_preview('暂无截图')

    def _update_cableway_tab(self, rows: List[Dict[str, Any]]) -> None:
        tree = self.cableway_tree
        if not tree:
            return
        self._cableway_cache = {}

        selected_iids = set(tree.selection())
        yview = tree.yview()
        self._safe_clear_tree(tree)

        fields = [
            'timestamp',
            'device_id',
            'location',
            'plc_host',
            'active_faults',
            'heartbeat_timeout',
            'q_fault',
            'q_forward',
            'q_reverse',
            'command_in_progress',
        ]

        for idx, row in enumerate(rows or []):
            status = row.get('status') if isinstance(row.get('status'), dict) else {}
            outputs = status.get('outputs') if isinstance(status.get('outputs'), dict) else {}
            active_faults = status.get('active_faults')
            if isinstance(active_faults, list):
                active_faults_text = '，'.join(str(item) for item in active_faults if item)
            else:
                active_faults_text = ''

            display = {
                'timestamp': row.get('timestamp') or status.get('timestamp'),
                'device_id': row.get('device_id'),
                'location': row.get('location'),
                'plc_host': row.get('plc_host') or status.get('plc_host') or status.get('host'),
                'active_faults': active_faults_text,
                'heartbeat_timeout': outputs.get('heartbeat_timeout'),
                'q_fault': outputs.get('q_fault'),
                'q_forward': outputs.get('q_forward'),
                'q_reverse': outputs.get('q_reverse'),
                'command_in_progress': outputs.get('command_in_progress'),
            }

            values = [self._format_cell(display.get(field)) for field in fields]
            iid = str(row.get('id') or idx)
            self._cableway_cache[iid] = row
            try:
                tree.insert('', tk.END, iid=iid, values=values)
            except tk.TclError:
                tree.insert('', tk.END, values=values)

            if iid in selected_iids:
                tree.selection_add(iid)

        if yview:
            tree.yview_moveto(yview[0])

        # 若用户没有选中项，则默认展示最新一条详情。
        if self.cableway_detail_text and not tree.selection() and rows:
            self._set_cableway_detail(rows[0])

    def _on_config_select(self) -> None:
        if not self.config_tree or not self.config_form_vars:
            return
        selection = self.config_tree.selection()
        if not selection:
            return
        item = self.config_tree.item(selection[0])
        values = item.get('values', [])
        cfg_type = values[0] if values else ''
        if cfg_type and cfg_type in self._config_cache:
            self._apply_config_form(self._config_cache[cfg_type])
            return
        data: Dict[str, Any] = {}
        for idx, field in enumerate(getattr(self, '_config_columns', [])):
            if idx < len(values):
                data[field] = values[idx]
        self._apply_config_form(data)

    def _on_config_type_change(self) -> None:
        if not self.config_form_vars:
            return
        cfg_type = self.config_form_vars['type'].get().strip()
        if cfg_type and cfg_type in self._config_cache:
            self._apply_config_form(self._config_cache[cfg_type])
        else:
            self._apply_config_form({'type': cfg_type})

    def _apply_config_form(self, data: Optional[Dict[str, Any]]) -> None:
        if not self.config_form_vars:
            return
        if not data:
            self._clear_config_form()
            return
        for field, var in self.config_form_vars.items():
            if field in data and data[field] is not None:
                var.set(str(data[field]))
            elif field == 'version':
                default_version = data.get('version', 0)
                var.set(str(default_version))
            else:
                if field != 'type':
                    var.set('')

    def _save_config(self) -> None:
        if not self.config_form_vars:
            return
        cfg_type = self.config_form_vars['type'].get().strip()
        if not cfg_type:
            messagebox.showwarning('提示', '请填写传感器类型')
            return
        try:
            min_threshold = float(self.config_form_vars['min_threshold'].get() or 0.0)
            max_threshold = float(self.config_form_vars['max_threshold'].get() or 0.0)
            version = int(self.config_form_vars['version'].get() or 0)
        except ValueError:
            messagebox.showerror('错误', '阈值与版本必须是数字')
            return

        payload = {
            'type': cfg_type,
            'description': self.config_form_vars['description'].get(),
            'unit': self.config_form_vars['unit'].get(),
            'min_threshold': min_threshold,
            'max_threshold': max_threshold,
            'version': version,
        }
        try:
            if cfg_type in self._config_cache:
                url = f"{self.base_url}/api/config/sensors/{cfg_type}"
                response = requests.put(url, json=payload, timeout=5)
            else:
                url = f"{self.base_url}/api/config/sensors"
                response = requests.post(url, json=payload, timeout=5)
            response.raise_for_status()
            self._append_log(f'阈值配置已保存: {cfg_type}')
            self._update_status('阈值配置已保存')
            threading.Thread(target=self._refresh_data, daemon=True).start()
        except requests.HTTPError as exc:
            messagebox.showerror('错误', f'保存失败: {exc.response.text}')
            self._append_log(f'保存阈值失败: {exc}')
        except Exception as exc:
            messagebox.showerror('错误', f'保存异常: {exc}')
            self._append_log(f'保存阈值异常: {exc}')

    def _delete_config(self) -> None:
        if not self.config_form_vars:
            return
        cfg_type = self.config_form_vars['type'].get().strip()
        if not cfg_type:
            messagebox.showwarning('提示', '请选择要删除的传感器类型')
            return
        if cfg_type not in self._config_cache:
            messagebox.showinfo('提示', '未找到配置记录。')
            return
        if not messagebox.askyesno('确认', f'确定删除 {cfg_type} 的阈值配置吗？'):
            return
        try:
            url = f"{self.base_url}/api/config/sensors/{cfg_type}"
            response = requests.delete(url, timeout=5)
            response.raise_for_status()
            self._append_log(f'阈值配置已删除: {cfg_type}')
            self._clear_config_form()
            threading.Thread(target=self._refresh_data, daemon=True).start()
        except requests.HTTPError as exc:
            messagebox.showerror('错误', f'删除失败: {exc.response.text}')
        except Exception as exc:
            messagebox.showerror('错误', f'删除异常: {exc}')

    def _clear_config_form(self) -> None:
        for var in self.config_form_vars.values():
            var.set('')
        if 'version' in self.config_form_vars:
            self.config_form_vars['version'].set('0')

    def _append_log(self, message: str) -> None:
        if not self.log_text:
            return
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f'[{timestamp}] {message}\n')
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def append_log_from_thread(self, message: str) -> None:
        """Thread-safe wrapper to append logs from background handlers."""
        self.root.after(0, lambda: self._append_log(message))

    def _update_status(self, message: str) -> None:
        self.root.after(0, lambda: self.status_var.set(message))

    def _on_image_selected(self) -> None:
        if not self.images_tree:
            return
        selection = self.images_tree.selection()
        if not selection:
            self._clear_image_preview('请选择一条记录')
            return
        iid = selection[0]
        record = self._image_cache.get(iid)
        if not record:
            self._clear_image_preview('记录已过期，请刷新')
            return
        encoded = record.get('image_data') or record.get('img')
        self._render_image_preview(encoded, record)

    def _render_image_preview(self, encoded: Optional[str], record: Dict[str, Any]) -> None:
        if not self.image_preview_label:
            return
        if not encoded:
            self._clear_image_preview('记录缺少截图数据')
            return
        try:
            buffer = base64.b64decode(encoded)
            image = Image.open(io.BytesIO(buffer))
            image.thumbnail((640, 360))
            photo = ImageTk.PhotoImage(image)
        except Exception as exc:
            logger.warning('Failed to decode snapshot: %s', exc)
            self._clear_image_preview('解码失败')
            return

        self._current_image_photo = photo
        title = record.get('image_name') or record.get('timestamp') or 'snapshot'
        self.image_preview_label.configure(image=photo, text=title, compound=tk.BOTTOM)

    def _clear_image_preview(self, message: str = '暂无截图') -> None:
        self._current_image_photo = None
        if self.image_preview_label:
            self.image_preview_label.configure(image='', text=message, compound=tk.CENTER)

    def _save_audio_thresholds(self) -> None:
        payload: Dict[str, float] = {}
        for key, var in self.audio_threshold_vars.items():
            try:
                payload[key] = float(var.get() or 0)
            except ValueError:
                payload[key] = 0.0
        try:
            resp = requests.post(self.base_url + '/api/audio/thresholds', json=payload, timeout=5)
            resp.raise_for_status()
            self._append_log('音频阈值已保存')
            self._update_status('音频阈值已保存')
        except Exception as exc:
            self._append_log(f'音频阈值保存失败: {exc}')
            messagebox.showerror('错误', f'音频阈值保存失败: {exc}')

    def _refresh_audio_metrics(self) -> None:
        try:
            if self._audio_monitor:
                data = self._audio_monitor.get_latest()
                if data:
                    self._audio_metrics_buffer.append(data)
                    if len(self._audio_metrics_buffer) > 200:
                        self._audio_metrics_buffer = self._audio_metrics_buffer[-200:]
                    self._update_audio_chart()
        except Exception as exc:
            logger.error('Failed to fetch audio metrics: %s', exc)

    def _update_audio_chart(self) -> None:
        if not self.audio_fig or not self.audio_canvas or not self.audio_lines:
            return
        if not self._audio_metrics_buffer:
            return
        metrics = self._audio_metrics_buffer[-100:]
        x = list(range(len(metrics)))
        for key, line in self.audio_lines.items():
            y = [item.get(key, 0.0) for item in metrics]
            line.set_data(x, y)
        ax = self.audio_fig.axes[0]
        ax.relim()
        ax.autoscale_view()
        self.audio_canvas.draw_idle()

    def _update_audio_table(self, rows: List[Dict[str, Any]]) -> None:
        self._audio_records = rows or []
        tree = self.audio_tree
        if not tree:
            return
        self._safe_clear_tree(tree)
        for idx, row in enumerate(self._audio_records):
            audio_id = str(row.get('id') or idx)
            size_kb = ''
            data = row.get('audio_data')
            if data:
                size_kb = f'{len(data) / 1024:.1f}'
            values = [
                self._format_cell(row.get('timestamp')),
                self._format_cell(row.get('device_id')),
                self._format_cell(row.get('location')),
                self._format_cell(row.get('audio_name')),
                size_kb,
            ]
            try:
                tree.insert('', tk.END, iid=audio_id, values=values)
            except tk.TclError:
                tree.insert('', tk.END, values=values)

    def _save_selected_audio(self) -> None:
        if not self.audio_tree:
            return
        selection = self.audio_tree.selection()
        if not selection:
            messagebox.showwarning('提示', '请先选择一条音频记录')
            return
        iid = selection[0]
        record = None
        for idx, row in enumerate(self._audio_records):
            rid = str(row.get('id') or idx)
            if rid == iid:
                record = row
                break
        if not record:
            messagebox.showerror('错误', '未找到所选音频记录')
            return
        data_b64 = record.get('audio_data')
        if not data_b64:
            messagebox.showerror('错误', '该记录不包含音频数据')
            return
        default_name = record.get('audio_name') or 'audio.wav'
        path = filedialog.asksaveasfilename(defaultextension='.wav', initialfile=default_name)
        if not path:
            return
        try:
            binary = base64.b64decode(data_b64)
            with open(path, 'wb') as f:
                f.write(binary)
            messagebox.showinfo('提示', '音频已保存。')
        except Exception as exc:
            messagebox.showerror('错误', f'保存失败: {exc}')

    def _play_selected_audio(self) -> None:
        if not self.audio_tree:
            return
        selection = self.audio_tree.selection()
        if not selection:
            messagebox.showwarning('提示', '请先选择一条音频记录')
            return
        record = self._find_audio_record(selection[0])
        if not record:
            messagebox.showerror('错误', '未找到所选音频记录')
            return
        data_b64 = record.get('audio_data')
        if not data_b64:
            messagebox.showerror('错误', '该记录不包含音频数据')
            return
        try:
            binary = base64.b64decode(data_b64)
        except Exception as exc:
            messagebox.showerror('错误', f'音频数据解码失败: {exc}')
            return

        import tempfile

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.wav')
        tmp.write(binary)
        tmp.flush()
        tmp.close()

        try:
            # Prefer ffplay if available for consistent playback
            self._stop_audio_playback()
            cmd = ['ffplay', '-nodisp', '-autoexit', tmp.name]
            self._audio_play_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except FileNotFoundError:
            messagebox.showerror('错误', '未找到 ffplay，请安装 ffmpeg 或手动保存文件播放')
        except Exception as exc:
            messagebox.showerror('错误', f'播放失败: {exc}')

    def _stop_audio_playback(self) -> None:
        proc = self._audio_play_proc
        self._audio_play_proc = None
        if not proc:
            return
        try:
            proc.terminate()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _find_audio_record(self, iid: str) -> Optional[Dict[str, Any]]:
        for idx, row in enumerate(self._audio_records):
            rid = str(row.get('id') or idx)
            if rid == iid:
                return row
        return None

    def _start_playback(self) -> None:
        self._stop_playback()
        url = self.playback_url.get().strip()
        if not url:
            messagebox.showwarning('提示', '请填写 RTSP URL')
            return
        # 使用 ffmpeg 解码帧并在 GUI 内嵌预览
        try:
            cmd = [
                'ffmpeg',
                '-loglevel',
                'quiet',
                '-fflags',
                'nobuffer',
                '-rtsp_transport',
                'tcp',
                '-rtsp_flags',
                'prefer_tcp',
                '-i',
                url,
                '-f',
                'image2pipe',
                '-pix_fmt',
                'rgb24',
                '-vcodec',
                'rawvideo',
                '-vf',
                'scale=640:360,fps=15',
                'pipe:1',
            ]
            self.playback_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self._append_log(f'启动内嵌播放: {url}')
            self._read_playback_frame()
        except FileNotFoundError:
            messagebox.showerror('错误', '未找到 ffmpeg，请确认已安装并加入 PATH')
        except Exception as exc:
            messagebox.showerror('错误', f'启动播放失败: {exc}')

    def _stop_playback(self) -> None:
        proc = self.playback_proc
        self.playback_proc = None
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        if self.playback_timer_id:
            self.root.after_cancel(self.playback_timer_id)
            self.playback_timer_id = None
        self.playback_photo = None
        if hasattr(self, 'playback_canvas') and self.playback_canvas:
            self.playback_canvas.delete('all')

    def _start_playback_external(self) -> None:
        self._stop_playback_external()
        url = self.playback_url.get().strip()
        if not url:
            messagebox.showwarning('提示', '请填写 RTSP URL')
            return
        try:
            # 使用 ffplay 外部窗口，保持窗口大小可调，默认不全屏
            self.playback_proc_external = subprocess.Popen(
                [
                    'ffplay',
                    '-loglevel',
                    'warning',
                    '-fflags',
                    'nobuffer',
                    '-rtsp_transport',
                    'tcp',
                    '-x',
                    '960',
                    '-y',
                    '540',
                    url,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._append_log(f'外部窗口播放: {url}')
        except FileNotFoundError:
            messagebox.showerror('错误', '未找到 ffplay，请确认已安装 FFmpeg 并加入 PATH')
        except Exception as exc:
            messagebox.showerror('错误', f'外部播放失败: {exc}')

    def _stop_playback_external(self) -> None:
        proc = self.playback_proc_external
        self.playback_proc_external = None
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def _read_playback_frame(self) -> None:
        if not self.playback_proc or self.playback_proc.poll() is not None:
            return
        stdout = self.playback_proc.stdout
        if not stdout:
            return
        target_w = max(1, self.playback_canvas.winfo_width())
        target_h = max(1, self.playback_canvas.winfo_height())
        src_w, src_h = 640, 360
        frame_size = src_w * src_h * 3
        try:
            data = stdout.read(frame_size)
            if not data or len(data) < frame_size:
                # 读取失败则稍后重试
                self.playback_timer_id = self.root.after(30, self._read_playback_frame)
                return
            image = Image.frombytes('RGB', (src_w, src_h), data)
            if target_w != src_w or target_h != src_h:
                image = image.resize((target_w, target_h), Image.BILINEAR)
            photo = ImageTk.PhotoImage(image)
            self.playback_photo = photo
            self.playback_canvas.create_image(0, 0, anchor=tk.NW, image=photo)
        except Exception as exc:
            logger.warning('播放帧读取失败: %s', exc)
        self.playback_timer_id = self.root.after(30, self._read_playback_frame)

    def _configure_logging(self) -> None:
        """Send INFO logs to GUI while limiting console output to errors."""
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)

        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)

        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.WARNING)
        console_handler.setFormatter(logging.Formatter('%(levelname)s:%(name)s:%(message)s'))
        root_logger.addHandler(console_handler)

        gui_handler = _GUILogHandler(self)
        root_logger.addHandler(gui_handler)

    # -- Cableway (PLC) ----------------------------------------------------
    def _on_cableway_param_select(self) -> None:
        key = (self.cableway_param_key.get() or '').strip()
        spec = FLOAT_PARAMS.get(key)
        if not spec:
            self.cableway_param_hint.set('')
            return
        range_text = spec.note or ''
        unit_text = f' ({spec.unit})' if spec.unit else ''
        hint = f'{spec.name}{unit_text}'
        if range_text:
            hint = f'{hint} | {range_text}'
        self.cableway_param_hint.set(hint)

    def _send_cableway_control(self, command_code: int) -> None:
        if not self._confirm_cableway_broadcast_if_no_device():
            return
        name = CONTROL_COMMANDS.get(int(command_code), str(command_code))
        request_id, body = self._build_cableway_command_body(
            command_type='control',
            payload={'command_code': int(command_code), 'pulse': True},
            notes_suffix=f'control={name}',
        )
        self._post_cableway_command(request_id, body)

    def _send_cableway_estop(self) -> None:
        if not self._confirm_cableway_broadcast_if_no_device():
            return
        if not messagebox.askyesno('确认', '确定发送【急停】吗？该操作会立即停止索道运动。'):
            return
        request_id, body = self._build_cableway_command_body(
            command_type='estop',
            payload={'pulse': True},
            notes_suffix='estop',
        )
        self._post_cableway_command(request_id, body)

    def _send_cableway_param(self) -> None:
        if not self._confirm_cableway_broadcast_if_no_device():
            return
        key = (self.cableway_param_key.get() or '').strip()
        if not key:
            messagebox.showwarning('提示', '请选择参数键')
            return
        raw_value = (self.cableway_param_value.get() or '').strip()
        if not raw_value:
            messagebox.showwarning('提示', '请输入参数数值')
            return
        try:
            value = float(raw_value)
        except ValueError:
            messagebox.showerror('错误', '参数数值必须是数字')
            return

        spec = FLOAT_PARAMS.get(key)
        spec_name = spec.name if spec else key
        request_id, body = self._build_cableway_command_body(
            command_type='set_params',
            payload={'params': {key: value}, 'pulse': True},
            notes_suffix=f'set_params {spec_name}={value}',
        )
        self._post_cableway_command(request_id, body)

    def _confirm_cableway_broadcast_if_no_device(self) -> bool:
        device_id = (self.cableway_device.get() or '').strip()
        if device_id:
            return True
        return bool(
            messagebox.askyesno(
                '确认',
                '未填写设备 ID，将通过 MQTT 广播到所有网关，可能导致多台设备同时动作。是否继续？',
            )
        )

    def _build_cableway_command_body(
        self,
        *,
        command_type: str,
        payload: Dict[str, Any],
        notes_suffix: str,
    ) -> tuple[str, Dict[str, Any]]:
        request_id = (self.cableway_request_id.get() or '').strip() or self._generate_request_id()
        body: Dict[str, Any] = {'type': command_type, 'request_id': request_id, **payload}
        device_id = (self.cableway_device.get() or '').strip()
        if device_id:
            body['device_id'] = device_id
        notes = (self.cableway_notes.get() or '').strip()
        if notes_suffix:
            notes = (notes + f' {notes_suffix}').strip()
        if notes:
            body['notes'] = notes
        return request_id, body

    def _post_cableway_command(self, request_id: str, body: Dict[str, Any]) -> None:
        try:
            response = requests.post(
                self.base_url + '/api/cableway/command',
                json=body,
                timeout=5,
            )
            response.raise_for_status()
            data = response.json()
            self._append_log(f'索道命令发送成功(request_id={request_id}): {json.dumps(data, ensure_ascii=False)}')
            self._update_status(f'索道命令发送成功 request_id={request_id}')
            self.cableway_request_id.set(self._generate_request_id())
            # 立即刷新一次状态/告警/命令历史
            threading.Thread(target=self._refresh_data, daemon=True).start()
        except requests.HTTPError as exc:
            messagebox.showerror('错误', f'索道命令发送失败: {exc.response.text}')
            self._append_log(f'索道命令发送失败(request_id={request_id}): {exc}')
        except Exception as exc:
            messagebox.showerror('错误', f'索道命令发送异常: {exc}')
            self._append_log(f'索道命令发送异常(request_id={request_id}): {exc}')

    def _on_cableway_selected(self) -> None:
        tree = self.cableway_tree
        if not tree:
            return
        selection = tree.selection()
        if not selection:
            self._set_cableway_detail(None)
            return
        iid = selection[0]
        record = self._cableway_cache.get(iid)
        self._set_cableway_detail(record)

    def _set_cableway_detail(self, record: Optional[Dict[str, Any]]) -> None:
        if not self.cableway_detail_text:
            return
        self.cableway_detail_text.configure(state=tk.NORMAL)
        self.cableway_detail_text.delete('1.0', tk.END)
        if not record:
            self.cableway_detail_text.insert(tk.END, '暂无数据')
        else:
            try:
                self.cableway_detail_text.insert(
                    tk.END,
                    json.dumps(record, ensure_ascii=False, indent=2, default=str),
                )
            except Exception:
                self.cableway_detail_text.insert(tk.END, str(record))
        self.cableway_detail_text.configure(state=tk.DISABLED)

    # -- Command handling ---------------------------------------------------
    def send_command(self) -> None:
        payload_text = self.command_payload.get().strip()
        if not payload_text:
            messagebox.showwarning('提示', '请填写命令 payload')
            return

        payload = self._normalize_payload(payload_text)
        request_id = (self.command_request_id.get() or '').strip() or self._generate_request_id()
        body: Dict[str, Any] = {'payload': payload, 'request_id': request_id}
        notes = (self.command_notes.get() or '').strip()
        if notes:
            body['notes'] = notes
        if request_id:
            # 确保日志/历史可定位请求
            body['notes'] = (body.get('notes', '') + f" request_id={request_id}").strip()
        if self.command_device.get():
            body['device_id'] = self.command_device.get().strip()

        try:
            response = requests.post(
                self.base_url + '/api/commands',
                json=body,
                timeout=5,
            )
            response.raise_for_status()
            data = response.json()
            self._append_log(f'命令发送成功(request_id={request_id}): {json.dumps(data, ensure_ascii=False)}')
            self._update_status(f'命令发送成功 request_id={request_id}')
            self._clear_command_inputs()
            # Refresh command history immediately
            threading.Thread(target=self._refresh_data, daemon=True).start()
        except requests.HTTPError as exc:
            messagebox.showerror('错误', f'命令发送失败: {exc.response.text}')
            self._append_log(f'命令发送失败(request_id={request_id}): {exc}')
        except Exception as exc:
            messagebox.showerror('错误', f'命令发送异常: {exc}')
            self._append_log(f'命令发送异常(request_id={request_id}): {exc}')

    def _normalize_payload(self, payload_text: str) -> Any:
        try:
            # Try parse as JSON list
            return json.loads(payload_text)
        except json.JSONDecodeError:
            return payload_text

    def _clear_command_inputs(self) -> None:
        self.command_payload.set('')
        self.command_notes.set('')
        self.command_device.set('')
        self.command_request_id.set(self._generate_request_id())

    # -- Helpers ------------------------------------------------------------
    @staticmethod
    def _format_cell(value: Any) -> str:
        if value is None:
            return ''
        if isinstance(value, float):
            return f'{value:.3f}'
        return str(value)

    @staticmethod
    def _extract_request_id(notes: str) -> Optional[str]:
        if not notes:
            return None
        for token in notes.split():
            if token.startswith('request_id='):
                return token.split('=', 1)[-1] or None
        return None

    @staticmethod
    def _generate_request_id() -> str:
        return f'gui-{int(time.time() * 1000)}'

    @staticmethod
    def _safe_clear_tree(tree: Optional[ttk.Treeview]) -> None:
        if not tree:
            return
        try:
            children = tree.get_children()
            if children:
                tree.delete(*children)
        except tk.TclError:
            return

    # -- BMS cell voltage alerts -------------------------------------------
    def _check_bms_cell_voltages(self, bms_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        alerts: List[Dict[str, Any]] = []
        threshold = 3.35
        now = time.time()
        for row in bms_rows:
            device = row.get('device_id') or 'bms'
            location = row.get('location') or ''
            cells = row.get('cell_voltages') or []
            if not isinstance(cells, list) or not cells:
                continue
            try:
                values = [float(v) for v in cells if v is not None]
            except (TypeError, ValueError):
                continue
            if not values:
                continue
            min_v = min(values)
            if min_v < threshold:
                cache_key = f'{device}@{location}'
                last = self._bms_alert_tracker.get(cache_key, 0.0)
                if now - last < 120:  # 防抖 2 分钟
                    continue
                self._bms_alert_tracker[cache_key] = now
                alerts.append(
                    {
                        'device_id': device,
                        'location': location,
                        'min_voltage': min_v,
                        'timestamp': row.get('timestamp'),
                    }
                )
                self._post_bms_alarm(device, location, min_v, threshold)
        return alerts

    def _handle_bms_alerts(self, alerts: List[Dict[str, Any]]) -> None:
        for alert in alerts:
            self._prompt_bms_charge(alert)

    def _prompt_bms_charge(self, alert: Dict[str, Any]) -> None:
        device = alert.get('device_id', 'bms')
        location = alert.get('location', '')
        min_v = alert.get('min_voltage', 0)
        window = tk.Toplevel(self.root)
        window.title('BMS 低压告警')
        window.geometry('360x180')
        tk.Label(
            window,
            text=f'设备 {device} @ {location}\n发现单体电压过低: {min_v:.3f} V\n请及时充电。',
            justify=tk.LEFT,
        ).pack(padx=16, pady=12)

        def on_charge() -> None:
            threading.Thread(target=self._send_bms_charge_command, args=(device,), daemon=True).start()
            window.destroy()

        actions = tk.Frame(window)
        actions.pack(pady=6)
        tk.Button(actions, text='一键充电', command=on_charge).pack(side=tk.LEFT, padx=6)
        tk.Button(actions, text='10 分钟后提醒', command=lambda: self._remind_charge_later(device, location, min_v)).pack(
            side=tk.LEFT, padx=6
        )
        tk.Button(actions, text='关闭', command=window.destroy).pack(side=tk.LEFT, padx=6)

    def _send_bms_charge_command(self, device_id: str) -> None:
        payload = "06 06 00 00 00 B1 48 09"
        body: Dict[str, Any] = {'payload': payload, 'notes': 'BMS低压一键充电', 'device_id': device_id}
        try:
            response = requests.post(self.base_url + '/api/commands', json=body, timeout=5)
            response.raise_for_status()
            self._append_log('一键充电指令已发送')
            self._update_status('已发送充电指令')
        except Exception as exc:
            self._append_log(f'一键充电指令发送失败: {exc}')

    def _post_bms_alarm(self, device_id: str, location: str, min_voltage: float, threshold: float) -> None:
        payload = {
            'sensor_key': 'cell_voltage',
            'sensor_name': 'BMS单体电压',
            'value': float(min_voltage),
            'unit': 'V',
            'min_threshold': float(threshold),
            'max_threshold': 0.0,
            'alarm_type': 'low',
            'is_handled': False,
            'location': location or '',
        }
        try:
            response = requests.post(self.base_url + '/api/alarms', json=payload, timeout=5)
            response.raise_for_status()
        except Exception as exc:
            self._append_log(f'BMS告警保存失败: {exc}')

    def _remind_charge_later(self, device_id: str, location: str, min_v: float) -> None:
        """Schedule a reminder popup after 10 minutes."""
        delay_ms = 10 * 60 * 1000
        self.root.after(
            delay_ms,
            messagebox.showinfo,
            '充电提醒',
            f'设备 {device_id} @ {location}\n单体电压最低 {min_v:.3f} V\n请确认是否已充电。',
        )

    # -- Run ----------------------------------------------------------------
    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    """Entry point for launching the GUI."""
    logging.basicConfig(level=logging.INFO)
    app = MonitoringGUI()
    app.run()


if __name__ == '__main__':
    main()
