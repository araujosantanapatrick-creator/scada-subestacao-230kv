# -*- coding: utf-8 -*-
"""
Sistema Supervisório para simulação SCADA 230kV (Diário de um Comissionador)
Copyright (C) 2026 Patrick Araujo Santana

Este arquivo é parte do projeto scada-subestacao-230kv.
Este programa é um software livre: você pode redistribuí-lo e/ou modificá-lo
sob os termos da Licença Pública Geral GNU (GPLv3) conforme publicada pela
Free Software Foundation.
"""
import random
import threading
import time
import tkinter as tk
from datetime import datetime

import customtkinter as ctk
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

try:
    from scapy.all import Ether, Raw, get_if_hwaddr, get_if_list, sendp
    SCAPY_AVAILABLE = True
except Exception:
    SCAPY_AVAILABLE = False

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


class SubstationSCADAApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Sistema SCADA / IEC 61850 - Subestação 230kV (Supervisório Completo)")
        self.geometry("1366x768")
        self.minsize(1024, 700)

        try:
            self.state("zoomed")
        except Exception:
            pass

        self.bay_status = {
            "52L1": {"name": "Disjuntor 52L1 (Linha)", "closed": True, "voltage": 230.0, "poles": {"A": True, "B": True, "C": True}, "discrepancy": False, "current": 180.0, "latency": 3.2},
            "52L2": {"name": "Disjuntor 52L2 (Transferência)", "closed": False, "voltage": 230.0, "poles": {"A": False, "B": False, "C": False}, "discrepancy": False, "current": 0.0, "latency": 3.5},
            "89BP": {"name": "Seccionadora de Bypass (89BP)", "closed": False, "voltage": 230.0, "poles": {"A": False, "B": False, "C": False}, "discrepancy": False, "current": 0.0, "latency": 2.5},
            "89T1": {"name": "Seccionadora Terra (89T1)", "closed": False, "voltage": 230.0, "poles": {"A": False, "B": False, "C": False}, "discrepancy": False, "current": 0.0, "latency": 2.8}
        }

        self.transf_status = {
            "temp": 65.5,
            "current": 180.0,
            "voltage": 230.0,
            "latency": 3.0,
            "tap": 6
        }

        self.goose_lock = threading.Lock()
        self.goose_st_num = 1
        self.goose_sq_num = 0
        self.goose_event_id = "ESTADO_INICIAL"
        self.goose_val_bool = True
        self.goose_last_change_ts = time.time()

        self.is_paused = False
        self.animation_toggle = False

        self.buffer_size = 120
        self.x_data = list(range(self.buffer_size))
        self.y_curr = [180.0 + random.uniform(-5, 5) for _ in range(self.buffer_size)]
        self.y_volt = [230.0 + random.uniform(-0.5, 0.5) for _ in range(self.buffer_size)]

        self.window_size = 30
        self.view_start = self.buffer_size - self.window_size

        self.cursor_x1 = self.buffer_size - 20
        self.cursor_x2 = self.buffer_size - 10

        interfaces_padrao = ["Wi-Fi", "Ethernet", "Ethernet 0", "VMnet1", "VMnet8"]
        if SCAPY_AVAILABLE:
            try:
                scapy_ifaces = get_if_list()
                self.lista_interfaces = list(dict.fromkeys(interfaces_padrao + scapy_ifaces))
            except Exception:
                self.lista_interfaces = interfaces_padrao
        else:
            self.lista_interfaces = interfaces_padrao

        default_iface = "Wi-Fi" if "Wi-Fi" in self.lista_interfaces else self.lista_interfaces[0]
        self.interface_selecionada = ctk.StringVar(value=default_iface)

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.create_header()
        self.create_tabview()
        self.update_telemetry()

        self.running = True
        self.thread_goose_hb = threading.Thread(target=self._loop_heartbeat_goose, daemon=True)
        self.thread_goose_hb.start()

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def on_close(self):
        self.running = False
        self.destroy()

    def create_header(self):
        header_frame = ctk.CTkFrame(self, height=55, corner_radius=0, fg_color=("#1f1f1f", "#141414"))
        header_frame.grid(row=0, column=0, sticky="ew")
        header_frame.grid_columnconfigure(2, weight=1)

        title_label = ctk.CTkLabel(header_frame, text="⚡ SUPERVISÓRIO SCADA 230kV — IEC 61850", font=ctk.CTkFont(size=15, weight="bold"))
        title_label.grid(row=0, column=0, padx=15, pady=10, sticky="w")

        iface_frame = ctk.CTkFrame(header_frame, fg_color="transparent")
        iface_frame.grid(row=0, column=1, padx=10, pady=5)

        lbl_iface = ctk.CTkLabel(iface_frame, text="Placa Rede:", font=ctk.CTkFont(size=11, weight="bold"), text_color="#3498db")
        lbl_iface.pack(side="left", padx=3)

        self.menu_iface = ctk.CTkComboBox(
            iface_frame,
            values=self.lista_interfaces,
            variable=self.interface_selecionada,
            width=160,
            height=28,
            font=ctk.CTkFont(size=11)
        )
        self.menu_iface.pack(side="left", padx=3)

        status_txt = "ESTADO: NORMAL (GOOSE ATIVO | Npcap)" if SCAPY_AVAILABLE else "ESTADO: NORMAL (Simulação)"
        self.status_box = ctk.CTkLabel(
            header_frame,
            text=status_txt,
            text_color="#00ff00" if SCAPY_AVAILABLE else "#f39c12",
            font=ctk.CTkFont(size=11, weight="bold")
        )
        self.status_box.grid(row=0, column=2, padx=15, pady=10, sticky="e")

    def create_tabview(self):
        self.tabview = ctk.CTkTabview(self, fg_color="#1a1a1a")
        self.tabview.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)

        self.tab_mimic = self.tabview.add("Diagrama Unifilar")
        self.tab_control = self.tabview.add("Telecomando & Inter-Bays")
        self.tab_prot = self.tabview.add("Proteção ANSI & TR1")
        self.tab_alarms = self.tabview.add("Painel de Alarmes")
        self.tab_trends = self.tabview.add("Gráficos de Tendência Multi-Variável")
        self.tab_nodes = self.tabview.add("Nós IEC 61850")
        self.tab_soe = self.tabview.add("Logger SOE")

        self.build_mimic_tab()
        self.build_control_tab()
        self.build_protection_tab()
        self.build_alarms_tab()
        self.build_trends_tab()
        self.build_nodes_tab()
        self.build_soe_tab()

    def build_mimic_tab(self):
        tab = self.tab_mimic
        tab.grid_rowconfigure(1, weight=1)
        tab.grid_columnconfigure(0, weight=1)

        title = ctk.CTkLabel(tab, text="Mimic Diagram - Pátio 230kV (Simbologia SCADA/SAGE: Disjuntores, Seccionadoras e TR1)", font=ctk.CTkFont(size=14, weight="bold"))
        title.grid(row=0, column=0, pady=10)

        self.canvas_mimic = tk.Canvas(tab, bg="#000000", highlightthickness=0)
        self.canvas_mimic.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
        self.canvas_mimic.bind("<Configure>", lambda _e: self.draw_mimic_diagram())

        btn_frame = ctk.CTkFrame(tab, fg_color="transparent")
        btn_frame.grid(row=2, column=0, pady=10)

        ctk.CTkButton(btn_frame, text="Atualizar Desenho", fg_color="#2980b9", command=self.draw_mimic_diagram).pack(side="left", padx=5)
        ctk.CTkButton(btn_frame, text="Restabelecer Subestação", fg_color="#16a085", command=self.reset_patio).pack(side="left", padx=5)
        ctk.CTkButton(btn_frame, text="Simular Discrepância de Polos (52L1)", fg_color="#d35400", command=self.trigger_pole_discrepancy).pack(side="left", padx=5)

    def is_bay_energized(self):
        b1_closed = self.bay_status["52L1"]["closed"]
        b2_closed = self.bay_status["52L2"]["closed"]
        bp_closed = self.bay_status["89BP"]["closed"]
        return b1_closed or (bp_closed and b2_closed)

    def _draw_breaker_52(self, c, cx, cy, size, is_closed, label, energized_green, discrepancy=False):
        half = size / 2
        fill_color = "#ff5252" if discrepancy else (energized_green if is_closed else "#c62828")
        outline_color = "#ffd600" if discrepancy else "#e0e0e0"
        c.create_rectangle(cx - half, cy - half, cx + half, cy + half, fill=fill_color, outline=outline_color, width=2)
        c.create_text(cx, cy, text=label, fill="#000000" if (is_closed and not discrepancy) else "white", font=("Consolas", 8, "bold"))

    def _draw_disconnector_89(self, c, x1, y1, x2, y2, is_closed, line_color, motorized=False):
        c.create_rectangle(x1 - 3, y1 - 3, x1 + 3, y1 + 3, fill="#e53935", outline="#e53935")
        c.create_rectangle(x2 - 3, y2 - 3, x2 + 3, y2 + 3, fill="#e53935", outline="#e53935")
        if is_closed:
            c.create_line(x1, y1, x2, y2, fill=line_color, width=3)
        else:
            dx = x2 - x1
            dy = y2 - y1
            if abs(dy) >= abs(dx):
                blade_x = x1 - abs(dy) * 0.45
                blade_y = y2
            else:
                blade_x = x2
                blade_y = y1 - abs(dx) * 0.45
            c.create_line(x1, y1, blade_x, blade_y, fill=line_color, width=3)
        if motorized:
            mx = (x1 + x2) / 2 - 14
            my = (y1 + y2) / 2
            c.create_text(mx, my, text="M", fill="#ffffff", font=("Consolas", 8, "bold"))

    def _draw_transformer_tr(self, c, cx, cy_top, cy_bot, r, color_top, color_bot, arrow_color):
        c.create_oval(cx - r, cy_top - r, cx + r, cy_top + r, outline=color_top, width=2.5)
        c.create_oval(cx - r, cy_bot - r, cx + r, cy_bot + r, outline=color_bot, width=2.5)
        c.create_line(cx - r * 1.15, cy_bot + r * 0.75, cx + r * 1.15, cy_top - r * 0.75, fill=arrow_color, width=2, arrow=tk.LAST)

    def draw_mimic_diagram(self):
        c = self.canvas_mimic
        c.delete("all")
        width = c.winfo_width()
        height = c.winfo_height()
        if width < 100:
            width = 900
        if height < 100:
            height = 500

        self.animation_toggle = not self.animation_toggle
        b1_closed = self.bay_status["52L1"]["closed"]
        b2_closed = self.bay_status["52L2"]["closed"]
        bp_closed = self.bay_status["89BP"]["closed"]
        b3_closed = self.bay_status["89T1"]["closed"]

        energized_green = "#00ff00"
        mv_color = "#ffff00"
        deenergized_color = "#455a64"

        c.create_line(width * 0.10, height * 0.09, width * 0.90, height * 0.09, fill=energized_green, width=5)
        c.create_text(width * 0.5, height * 0.04, text="BARRAMENTO PRINCIPAL 230kV [ENERGIZADO]", fill=energized_green, font=("Consolas", 10, "bold"))

        transf_bar_active = b2_closed
        transf_bar_color = energized_green if transf_bar_active else deenergized_color
        transf_bar_text_status = "ENERGIZADO" if transf_bar_active else "DESENERGIZADO"
        transf_bar_text_color = energized_green if transf_bar_active else "#e53935"

        c.create_line(width * 0.10, height * 0.23, width * 0.90, height * 0.23, fill=transf_bar_color, width=3, dash=(5, 3))
        c.create_text(width * 0.5, height * 0.18, text=f"BARRAMENTO DE TRANSFERÊNCIA 230kV [{transf_bar_text_status}]", fill=transf_bar_text_color, font=("Consolas", 9, "bold"))

        b_size = min(width, height) * 0.085
        b_size = max(36, min(54, b_size))

        line1_color = energized_green if b1_closed else deenergized_color
        line2_color = energized_green if b2_closed else deenergized_color

        c.create_line(width * 0.35, height * 0.09, width * 0.35, height * 0.36 - b_size / 2, fill=energized_green, width=3)
        c.create_line(width * 0.65, height * 0.23, width * 0.65, height * 0.36 - b_size / 2, fill=line2_color, width=3)

        b1_disc = self.bay_status["52L1"]["discrepancy"]
        self._draw_breaker_52(c, width * 0.35, height * 0.36, b_size, b1_closed, "52L1", energized_green, discrepancy=b1_disc)
        self._draw_breaker_52(c, width * 0.65, height * 0.36, b_size, b2_closed, "52L2", energized_green, discrepancy=False)

        bp_line_color = energized_green if (bp_closed and transf_bar_active) else deenergized_color
        c.create_line(width * 0.65, height * 0.23, width * 0.22, height * 0.23, fill=bp_line_color, width=2, dash=(3, 2))
        self._draw_disconnector_89(c, width * 0.22, height * 0.23, width * 0.22, height * 0.36, bp_closed, bp_line_color, motorized=False)
        c.create_line(width * 0.22, height * 0.36, width * 0.35 - b_size / 2, height * 0.36, fill=bp_line_color, width=2, dash=(3, 2))
        c.create_text(width * 0.16, height * 0.30, text=f"89BP ({'FECH.' if bp_closed else 'AB.'})", fill="#ffffff", font=("Consolas", 8, "bold"))

        c.create_line(width * 0.35, height * 0.36 + b_size / 2, width * 0.35, height * 0.50, fill=line1_color, width=3)
        c.create_line(width * 0.65, height * 0.36 + b_size / 2, width * 0.65, height * 0.50, fill=line2_color, width=3)

        bus_inf_active = self.is_bay_energized()
        bus_inf_color = energized_green if bus_inf_active else deenergized_color

        c.create_line(width * 0.35, height * 0.50, width * 0.65, height * 0.50, fill=bus_inf_color, width=3)
        c.create_line(width * 0.50, height * 0.50, width * 0.50, height * 0.72, fill=bus_inf_color, width=3)

        c.create_line(width * 0.50, height * 0.60, width * 0.61, height * 0.60, fill=bus_inf_color, width=2)
        t1_line_color = energized_green if (b3_closed and bus_inf_active) else deenergized_color
        self._draw_disconnector_89(c, width * 0.61, height * 0.60, width * 0.71, height * 0.60, b3_closed, t1_line_color, motorized=False)
        c.create_text(width * 0.66, height * 0.56, text=f"89T1 ({'TERRA' if b3_closed else 'AB.'})", fill="#ffffff", font=("Consolas", 8, "bold"))

        c.create_line(width * 0.71, height * 0.60, width * 0.71, height * 0.69, fill="#cfd8dc", width=2)
        c.create_line(width * 0.67, height * 0.69, width * 0.75, height * 0.69, fill="#cfd8dc", width=2)
        c.create_line(width * 0.685, height * 0.71, width * 0.735, height * 0.71, fill="#cfd8dc", width=2)
        c.create_line(width * 0.70, height * 0.73, width * 0.72, height * 0.73, fill="#cfd8dc", width=1.5)

        tr_color_top = energized_green if bus_inf_active else deenergized_color
        tr_color_bot = mv_color if bus_inf_active else deenergized_color
        tr_arrow_color = energized_green if bus_inf_active else deenergized_color
        tr_text_status = "ENERGIZADO" if bus_inf_active else "DESENERGIZADO"
        current_val = self.transf_status["current"] if bus_inf_active else 0.0
        r_tr = min(width, height) * 0.045
        r_tr = max(20, min(32, r_tr))

        self._draw_transformer_tr(c, width * 0.50, height * 0.76, height * 0.76 + r_tr * 1.45, r_tr, tr_color_top, tr_color_bot, tr_arrow_color)

        box_x1 = width * 0.50 + r_tr + 16
        box_y1 = height * 0.71
        box_x2 = box_x1 + 195
        box_y2 = box_y1 + 88
        c.create_rectangle(box_x1, box_y1, box_x2, box_y2, fill="#0d1117", outline="#37474f", width=1.5)
        c.create_text(box_x1 + 10, box_y1 + 14, anchor="w", text=f"TR1: {self.transf_status['voltage']:.1f} kV | TAP {self.transf_status['tap']}", fill=energized_green if bus_inf_active else "#e53935", font=("Consolas", 8, "bold"))
        c.create_text(box_x1 + 10, box_y1 + 32, anchor="w", text=f"CORR: {current_val:.1f} A | TEMP: {self.transf_status['temp']}°C", fill="#ffd600" if bus_inf_active else "#90a4ae", font=("Consolas", 8))
        c.create_text(box_x1 + 10, box_y1 + 50, anchor="w", text=f"ESTADO: {tr_text_status}", fill=energized_green if bus_inf_active else "#e53935", font=("Consolas", 8, "bold"))
        c.create_text(box_x1 + 10, box_y1 + 68, anchor="w", text=f"LATÊNCIA: {self.transf_status['latency']} ms", fill="#64b5f6", font=("Consolas", 8))

        c.create_text(width * 0.50, height * 0.95, text=f"TR1 (230/13.8kV) [{tr_text_status}] | Temp: {self.transf_status['temp']}°C | Corr: {current_val}A | Latência: {self.transf_status['latency']}ms", fill=energized_green if bus_inf_active else "#e53935", font=("Consolas", 9, "bold"))

    def build_control_tab(self):
        tab = self.tab_control
        ctk.CTkLabel(tab, text="Módulo de Telecomando e Intertravamentos Lógicos CILO (230kV)", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=15)

        frame_cmds = ctk.CTkScrollableFrame(tab, fg_color="#1f1f1f", height=450)
        frame_cmds.pack(fill="both", expand=True, padx=20, pady=10)

        ctk.CTkLabel(frame_cmds, text="Comandos Diretos Independentes (Destaque invertido conforme o estado atual):", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=15, pady=10)

        self.control_buttons = {}

        for bay_id in ["52L1", "52L2", "89BP", "89T1"]:
            data = self.bay_status[bay_id]
            sub = ctk.CTkFrame(frame_cmds, fg_color="#262626")
            sub.pack(fill="x", padx=15, pady=5)
            ctk.CTkLabel(sub, text=f"{data['name']} (Un: {data['voltage']}kV) | Latência: {data['latency']}ms", font=ctk.CTkFont(size=11, family="Consolas")).pack(side="left", padx=10, pady=10)

            btn_box = ctk.CTkFrame(sub, fg_color="transparent")
            btn_box.pack(side="right", padx=10, pady=5)

            btn_abrir = ctk.CTkButton(btn_box, text="ABRIR", width=80, command=lambda b=bay_id: self.execute_command(b, False))
            btn_abrir.pack(side="left", padx=5)

            btn_fechar = ctk.CTkButton(btn_box, text="FECHAR", width=80, command=lambda b=bay_id: self.execute_command(b, True))
            btn_fechar.pack(side="left", padx=5)

            self.control_buttons[bay_id] = {"abrir": btn_abrir, "fechar": btn_fechar}

        self.update_control_buttons_visual()

    def update_control_buttons_visual(self):
        open_red = "#c62828"
        open_red_hover = "#9e1c1c"
        close_green = "#1b8a4b"
        close_green_hover = "#146c3a"
        disabled_gray = "#4a4a4a"

        for bay_id, btns in self.control_buttons.items():
            is_closed = self.bay_status[bay_id]["closed"]
            if is_closed:
                btns["abrir"].configure(fg_color=open_red, hover_color=open_red_hover, text_color="white", border_width=2, border_color="#ef9a9a")
                btns["fechar"].configure(fg_color=disabled_gray, hover_color=disabled_gray, text_color="#9e9e9e", border_width=0)
            else:
                btns["fechar"].configure(fg_color=close_green, hover_color=close_green_hover, text_color="white", border_width=2, border_color="#a5d6a7")
                btns["abrir"].configure(fg_color=disabled_gray, hover_color=disabled_gray, text_color="#9e9e9e", border_width=0)

    def build_protection_tab(self):
        tab = self.tab_prot
        tab.grid_rowconfigure(2, weight=1)
        tab.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(tab, text="Módulo de Proteção ANSI Completa - Funções Dedicadas com Atuação no Pátio", font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, pady=10)

        p_frame = ctk.CTkFrame(tab, fg_color="#1f1f1f")
        p_frame.grid(row=1, column=0, sticky="ew", padx=20, pady=5)

        self.lbl_prot_status = ctk.CTkLabel(p_frame, text="Relés ANSI (50/51, 21, 87T, 67, 25, 59): OPERANDO DENTRO DOS LIMITES", font=ctk.CTkFont(size=11, family="Consolas"), text_color="#00ff00")
        self.lbl_prot_status.pack(padx=20, pady=15)

        btn_grid = ctk.CTkFrame(p_frame, fg_color="transparent")
        btn_grid.pack(padx=20, pady=(0, 15))

        ctk.CTkButton(btn_grid, text="Trip 50/51 (Sobrecorrente)", fg_color="#c0392b", command=lambda: self.simulate_ansi_trip("50/51", "Abertura 52L1 por Sobrecorrente")).pack(side="left", padx=4)
        ctk.CTkButton(btn_grid, text="Trip 87T (Diferencial)", fg_color="#c0392b", command=lambda: self.simulate_ansi_trip("87T", "Trip Diferencial TR1")).pack(side="left", padx=4)
        ctk.CTkButton(btn_grid, text="Trip 21 (Distância)", fg_color="#d35400", command=lambda: self.simulate_ansi_trip("21", "Proteção de Distância Zona 1")).pack(side="left", padx=4)
        ctk.CTkButton(btn_grid, text="Trip 67 (Direcional)", fg_color="#e67e22", command=lambda: self.simulate_ansi_trip("67", "Sobrecorrente Direcional")).pack(side="left", padx=4)
        ctk.CTkButton(btn_grid, text="Trip 25 (Sincronismo)", fg_color="#8e44ad", command=lambda: self.simulate_ansi_trip("25", "Checagem de Sincronismo")).pack(side="left", padx=4)
        ctk.CTkButton(btn_grid, text="Trip 59 (Sobretenso)", fg_color="#2980b9", command=lambda: self.simulate_ansi_trip("59", "Proteção de Sobretensão 230kV")).pack(side="left", padx=4)

        log_frame = ctk.CTkFrame(tab, fg_color="#141414")
        log_frame.grid(row=2, column=0, sticky="nsew", padx=20, pady=10)
        log_frame.grid_rowconfigure(1, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(log_frame, text="Área de Registros e Logs de Disparos de Todas as Funções ANSI (Trip Log / SOE):", font=ctk.CTkFont(size=12, weight="bold")).grid(row=0, column=0, sticky="w", padx=10, pady=5)

        self.prot_log_box = ctk.CTkTextbox(log_frame, font=ctk.CTkFont(family="Consolas", size=10), fg_color="#1a1a1a")
        self.prot_log_box.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self.prot_log_box.insert("0.0", f"[{datetime.now().strftime('%H:%M:%S')}] [PROT] Matriz de relés ANSI dedicada armada e ativa na subestação 230kV.\n")
        self.prot_log_box.configure(state="disabled")

    def build_alarms_tab(self):
        tab = self.tab_alarms
        tab.grid_rowconfigure(1, weight=1)
        tab.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(tab, text="Painel Annunciator - Alertas e Faltas Críticas (IEC 61850)", font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, pady=10)

        self.alarm_box = ctk.CTkTextbox(tab, font=ctk.CTkFont(family="Consolas", size=11))
        self.alarm_box.grid(row=1, column=0, sticky="nsew", padx=15, pady=5)
        self.alarm_box.insert("0.0", f"[{datetime.now().strftime('%H:%M:%S')}] [SYSTEM] Supervisório SCADA 230kV iniciado com sucesso.\n")
        self.alarm_box.configure(state="disabled")

        ctk.CTkButton(tab, text="Reconhecer Alarmes (ACK)", fg_color="#f39c12", text_color="black", command=self.ack_alarms).grid(row=2, column=0, pady=10)

    def build_trends_tab(self):
        tab = self.tab_trends
        tab.grid_rowconfigure(1, weight=1)
        tab.grid_columnconfigure(0, weight=1)

        header_tr = ctk.CTkFrame(tab, fg_color="transparent")
        header_tr.grid(row=0, column=0, sticky="ew", padx=15, pady=5)

        ctk.CTkLabel(header_tr, text="Gráficos de Tendência Estilo SAGE (Buffer Histórico & Deslocamento Temporal)", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left")

        ctrl_frame = ctk.CTkFrame(header_tr, fg_color="transparent")
        ctrl_frame.pack(side="right")

        self.btn_pause_trend = ctk.CTkButton(ctrl_frame, text="Pausar / Analisar", width=110, command=self.toggle_pause_trend)
        self.btn_pause_trend.pack(side="left", padx=5)

        ctk.CTkButton(ctrl_frame, text="◀ Histórico", width=80, fg_color="#7f8c8d", command=lambda: self.pan_history(-5)).pack(side="left", padx=2)
        ctk.CTkButton(ctrl_frame, text="Histórico ▶", width=80, fg_color="#7f8c8d", command=lambda: self.pan_history(5)).pack(side="left", padx=2)

        ctk.CTkButton(ctrl_frame, text="◀ Pena 1", width=70, command=lambda: self.move_cursor(1, -1)).pack(side="left", padx=2)
        ctk.CTkButton(ctrl_frame, text="Pena 1 ▶", width=70, command=lambda: self.move_cursor(1, 1)).pack(side="left", padx=2)
        ctk.CTkButton(ctrl_frame, text="◀ Pena 2", width=70, command=lambda: self.move_cursor(2, -1)).pack(side="left", padx=2)
        ctk.CTkButton(ctrl_frame, text="Pena 2 ▶", width=70, command=lambda: self.move_cursor(2, 1)).pack(side="left", padx=2)

        self.fig, self.ax1 = plt.subplots(figsize=(6, 3.2), dpi=100)
        self.fig.patch.set_facecolor('#1a1a1a')
        self.ax1.set_facecolor('#111111')

        self.ax2 = self.ax1.twinx()

        self.line1, = self.ax1.plot(self.x_data, self.y_curr, color='#3498db', label='Corrente L1 (A)')
        self.line2, = self.ax2.plot(self.x_data, self.y_volt, color='#e67e22', label='Tensão Nominal (kV)')

        self.ax1.set_ylabel('Corrente [A]', color='#3498db', fontsize=9, fontweight='bold')
        self.ax2.set_ylabel('Tensão [kV]', color='#e67e22', fontsize=9, fontweight='bold')
        self.ax1.set_xlabel('Buffer Amostral Histórico (Tempo)', color='white', fontsize=9)

        self.ax1.set_xlim(self.view_start, self.view_start + self.window_size)

        self.v1 = self.ax1.axvline(x=self.cursor_x1, color='#e74c3c', linestyle='--', linewidth=2, label='Pena 1 (Início Falta)')
        self.v2 = self.ax1.axvline(x=self.cursor_x2, color='#00ff00', linestyle='--', linewidth=2, label='Pena 2 (Fim Falta)')

        self.ax1.tick_params(colors='white', labelsize=8)
        self.ax2.tick_params(colors='white', labelsize=8)
        self.ax1.xaxis.label.set_color('white')
        for spine in self.ax1.spines.values():
            spine.set_color('#555555')
        for spine in self.ax2.spines.values():
            spine.set_color('#555555')

        lines_1, labels_1 = self.ax1.get_legend_handles_labels()
        lines_2, labels_2 = self.ax2.get_legend_handles_labels()
        self.ax1.legend(lines_1 + lines_2, labels_1 + labels_2, loc='upper left', fontsize=7, facecolor='#1f1f1f', labelcolor='white')

        self.canvas = FigureCanvasTkAgg(self.fig, master=tab)
        self.canvas.get_tk_widget().grid(row=1, column=0, sticky="nsew", padx=10, pady=5)

        self.toolbar_frame = ctk.CTkFrame(tab, fg_color="transparent")
        self.toolbar_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 5))
        self.toolbar = NavigationToolbar2Tk(self.canvas, self.toolbar_frame)
        self.toolbar.update()

    def pan_history(self, delta):
        if not self.is_paused:
            self.log_alarm("[ANÁLISE SAGE] Pause o gráfico para navegar livremente pelo histórico de tempo.\n")
            return

        new_start = self.view_start + delta
        max_start = self.buffer_size - self.window_size
        self.view_start = max(0, min(max_start, new_start))

        self.ax1.set_xlim(self.view_start, self.view_start + self.window_size)
        self.canvas.draw()
        self.log_alarm(f"[ANÁLISE SAGE] Deslocamento temporal do histórico: Janela exibida de t={self.view_start} até t={self.view_start + self.window_size}s.\n")

    def move_cursor(self, pena_id, delta):
        max_idx = self.buffer_size - 1
        if pena_id == 1:
            self.cursor_x1 = max(0, min(max_idx, self.cursor_x1 + delta))
            self.v1.set_xdata([self.cursor_x1, self.cursor_x1])
        else:
            self.cursor_x2 = max(0, min(max_idx, self.cursor_x2 + delta))
            self.v2.set_xdata([self.cursor_x2, self.cursor_x2])

        self.canvas.draw()

        dt_diff = abs(self.cursor_x2 - self.cursor_x1)
        curr_p1 = self.y_curr[self.cursor_x1]
        volt_p1 = self.y_volt[self.cursor_x1]
        curr_p2 = self.y_curr[self.cursor_x2]
        volt_p2 = self.y_volt[self.cursor_x2]

        self.log_alarm(f"[ANÁLISE SAGE] Δt={dt_diff}s | Pena 1 (idx {self.cursor_x1}): {curr_p1:.1f}A, {volt_p1:.2f}kV | Pena 2 (idx {self.cursor_x2}): {curr_p2:.1f}A, {volt_p2:.2f}kV\n")

    def build_nodes_tab(self):
        tab = self.tab_nodes
        tab.grid_rowconfigure(1, weight=1)
        tab.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(tab, text="Árvore de Nós Lógicos IEC 61850 (Subestação 230kV)", font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, pady=10)

        tree_box = ctk.CTkTextbox(tab, font=ctk.CTkFont(family="Consolas", size=11), fg_color="#111111")
        tree_box.grid(row=1, column=0, sticky="nsew", padx=15, pady=10)

        tree_text = (
            "📁 E230kV_Substation\n"
            " └─ 📁 Bay_L1 (230kV)\n"
            "    ├─ 📄 XCBR1.Pos.stVal (Disjuntor Tripolar A/B/C)\n"
            "    ├─ 📄 CSWI1.Pos.ctlModel (Controle Operacional / CILO Interlocks)\n"
            "    ├─ 📄 MMXU1.A.phsA/B/C (Medição Fasorial de Corrente e Tensão)\n"
            "    └─ 🌐 GOOSE Dataset (Latência de Publication: ~3.1ms)\n"
            " └─ 📁 Bay_TR1 (230/13.8kV)\n"
            "    ├─ 📄 PTOC1, PDIS1, RREC1, TTRC1 (Funções ANSI)\n"
            "    └─ 🌐 GOOSE Trip Command (Latência de Rede: ~2.1ms)\n"
        )
        tree_box.insert("0.0", tree_text)
        tree_box.configure(state="disabled")

    def build_soe_tab(self):
        tab = self.tab_soe
        tab.grid_rowconfigure(1, weight=1)
        tab.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(tab, text="Logger de Sequência de Eventos (SOE - Sequence of Events)", font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, pady=10)

        self.soe_box = ctk.CTkTextbox(tab, font=ctk.CTkFont(family="Consolas", size=10), fg_color="#111111")
        self.soe_box.grid(row=1, column=0, sticky="nsew", padx=15, pady=10)
        self.soe_box.insert("0.0", f"[{datetime.now().strftime('%H:%M:%S.000')}] [SOE] Inicialização completa dos relés ANSI e lógica CILO.\n")
        self.soe_box.configure(state="disabled")

    def _ber_len(self, length):
        if length < 128:
            return bytes([length])
        if length < 256:
            return bytes([0x81, length])
        return bytes([0x82, length >> 8, length & 0xFF])

    def _ber_tlv(self, tag_bytes, value_bytes):
        return tag_bytes + self._ber_len(len(value_bytes)) + value_bytes

    def _get_source_mac(self, iface_name):
        if SCAPY_AVAILABLE and iface_name:
            try:
                mac = get_if_hwaddr(iface_name)
                if mac and mac != "00:00:00:00:00:00":
                    return mac
            except Exception:
                pass
        return "02:42:ac:11:00:02"

    def _build_and_send_goose_frame(self, iface_name, event_id, st_num, sq_num, val_bool, tal_ms=2000):
        gocb_ref = self._ber_tlv(b'\x80', b"E230kV_Substation/LLN0$GO$gcb01")
        time_live = self._ber_tlv(b'\x81', int(tal_ms).to_bytes(2, 'big'))
        dat_set = self._ber_tlv(b'\x82', b"E230kV_Substation/LLN0$DS_Bay")
        go_id = self._ber_tlv(b'\x83', event_id.encode('ascii', errors='ignore')[:64])

        now_sec = int(time.time())
        frac_24 = int((time.time() - now_sec) * (1 << 24)) & 0xFFFFFF
        utc_bytes = now_sec.to_bytes(4, 'big') + frac_24.to_bytes(3, 'big') + b'\x00'
        t_elem = self._ber_tlv(b'\x84', utc_bytes)

        st_elem = self._ber_tlv(b'\x85', int(st_num).to_bytes(4, 'big').lstrip(b'\x00') or b'\x00')
        sq_elem = self._ber_tlv(b'\x86', int(sq_num).to_bytes(4, 'big').lstrip(b'\x00') or b'\x00')
        sim_elem = self._ber_tlv(b'\x87', b'\x00')
        conf_rev = self._ber_tlv(b'\x88', b'\x01')
        nds_com = self._ber_tlv(b'\x89', b'\x00')
        num_entries = self._ber_tlv(b'\x8A', b'\x01')

        bool_val_bytes = b'\xff' if val_bool else b'\x00'
        data_bool = self._ber_tlv(b'\x83', bool_val_bytes)
        all_data = self._ber_tlv(b'\xAB', data_bool)

        pdu_content = (
            gocb_ref + time_live + dat_set + go_id + t_elem +
            st_elem + sq_elem + sim_elem + conf_rev + nds_com +
            num_entries + all_data
        )
        goose_pdu = self._ber_tlv(b'\x61', pdu_content)

        pdu_total_len = len(goose_pdu) + 8
        fixed_header = b'\x00\x01' + pdu_total_len.to_bytes(2, 'big') + b'\x00\x00\x00\x00'
        payload_completo = fixed_header + goose_pdu

        src_mac = self._get_source_mac(iface_name)
        ether_frame = Ether(src=src_mac, dst="01:0c:cd:01:00:00", type=0x88B8) / Raw(load=payload_completo)
        sendp(ether_frame, iface=iface_name, verbose=False)

    def enviar_pacote_goose(self, evento, status):
        if not SCAPY_AVAILABLE:
            return
        evento_upper = str(evento).upper()
        status_upper = str(status).upper()
        is_false_event = (
            evento_upper.startswith("TRIP_")
            or "TRIP" in evento_upper
            or any(tag in status_upper for tag in ("ABERTO", "ALERTA", "TRIP"))
        )
        val_bool = not is_false_event
        iface_escolhida = self.interface_selecionada.get().strip()
        if not iface_escolhida:
            return

        with self.goose_lock:
            self.goose_st_num += 1
            self.goose_sq_num = 0
            self.goose_event_id = evento
            self.goose_val_bool = val_bool
            self.goose_last_change_ts = time.time()
            st_atual = self.goose_st_num
            sq_atual = self.goose_sq_num

        try:
            self._build_and_send_goose_frame(iface_escolhida, evento, st_atual, sq_atual, val_bool)
        except Exception:
            pass

    def _loop_heartbeat_goose(self):
        while self.running:
            time.sleep(2.0)
            if not self.running or not SCAPY_AVAILABLE:
                continue
            iface_escolhida = self.interface_selecionada.get().strip()
            if not iface_escolhida:
                continue

            with self.goose_lock:
                self.goose_sq_num += 1
                st_atual = self.goose_st_num
                sq_atual = self.goose_sq_num
                ev_atual = self.goose_event_id
                val_atual = self.goose_val_bool

            try:
                self._build_and_send_goose_frame(iface_escolhida, ev_atual, st_atual, sq_atual, val_atual)
            except Exception:
                pass

    def execute_command(self, bay_id, should_close):
        if should_close and bay_id == "89T1":
            if self.is_bay_energized() or self.bay_status["89BP"]["closed"]:
                timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                msg = f"[{timestamp}] INTERTRAVAMENTO CILO: Bloqueio! Tentativa de fechar a Seccionadora Terra (89T1) com barramento/linha energizados.\n"
                self.log_alarm(msg)
                self.log_soe(msg)
                return

        if should_close and bay_id == "89BP":
            if not self.bay_status["52L2"]["closed"]:
                timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                msg = f"[{timestamp}] INTERTRAVAMENTO CILO: Bloqueio! Tentativa de fechar o Bypass (89BP) com o Barramento de Transferência desenergizado (52L2 aberto).\n"
                self.log_alarm(msg)
                self.log_soe(msg)
                return

        if not should_close and bay_id == "89BP":
            if self.is_bay_energized() and not self.bay_status["52L1"]["closed"]:
                timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                msg = f"[{timestamp}] INTERTRAVAMENTO CILO: Alarme! Tentativa de abrir a Seccionadora de Bypass (89BP) sob carga!\n"
                self.log_alarm(msg)
                self.log_soe(msg)
                return

        bay = self.bay_status[bay_id]
        bay["closed"] = should_close
        status_str = "FECHADO" if should_close else "ABERTO"
        for p in bay["poles"]:
            bay["poles"][p] = should_close
        bay["discrepancy"] = False
        bay["latency"] = round(random.uniform(2.8, 3.6), 1)

        if self.is_bay_energized():
            self.transf_status["current"] = 180.0
            self.transf_status["voltage"] = 230.0
        else:
            self.transf_status["current"] = 0.0
            self.transf_status["voltage"] = 0.0

        self.draw_mimic_diagram()
        self.update_control_buttons_visual()

        self.enviar_pacote_goose(evento=f"POS_{bay_id}", status=status_str)

        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        msg = f"[{timestamp}] COMANDO: {bay['name']} comutado para {status_str} | Latência GOOSE/MMS: {bay['latency']}ms.\n"
        self.log_alarm(msg)
        self.log_soe(msg)

    def trigger_pole_discrepancy(self):
        bay = self.bay_status["52L1"]
        bay["poles"]["A"] = True
        bay["poles"]["B"] = False
        bay["poles"]["C"] = True
        bay["discrepancy"] = True
        bay["latency"] = 11.4

        self.draw_mimic_diagram()
        self.update_control_buttons_visual()

        self.enviar_pacote_goose(evento="DISCREPANCIA_POLOS_52L1", status="ALERTA")

        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        msg = f"[{timestamp}] ALARME CRÍTICO: Discrepância de Polos no Disjuntor 52L1 (Fase B aberta em desacordo) [230kV]!\n"
        self.status_box.configure(text="ESTADO: ALARME DE DISCREPÂNCIA DE POLOS", text_color="#e74c3c")
        self.log_alarm(msg)
        self.log_soe(msg)

    def simulate_ansi_trip(self, ansi_code, description):
        self.transf_status["current"] = 0.0
        self.transf_status["voltage"] = 0.0
        self.transf_status["latency"] = 1.9

        self.bay_status["52L1"]["closed"] = False
        for p in self.bay_status["52L1"]["poles"]:
            self.bay_status["52L1"]["poles"][p] = False

        self.lbl_prot_status.configure(text=f"TRIP ATUADO: ANSI {ansi_code} ({description})!", text_color="#e74c3c")
        self.draw_mimic_diagram()
        self.update_control_buttons_visual()

        self.enviar_pacote_goose(evento=f"TRIP_ANSI_{ansi_code}", status="ATIVO")

        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        msg = f"[{timestamp}] PROTEÇÃO: Atuação da Função ANSI [{ansi_code} - {description}] | Disjuntor 52L1 Aberto por GOOSE (Corrente e Tensão Zeradas).\n"
        self.log_alarm(msg)
        self.log_soe(msg)
        self.log_prot(f"[{timestamp}] TRIP ANSI {ansi_code} ({description}) | GOOSE Trip Enviado (Latência: 1.9ms).\n")

    def reset_patio(self):
        self.bay_status["52L1"]["closed"] = True
        self.bay_status["52L2"]["closed"] = False
        self.bay_status["89BP"]["closed"] = False
        self.bay_status["89T1"]["closed"] = False
        for b in self.bay_status.values():
            for p in b["poles"]:
                b["poles"][p] = b["closed"]
            b["discrepancy"] = False
            b["latency"] = round(random.uniform(2.8, 3.4), 1)

        self.transf_status["current"] = 180.0
        self.transf_status["voltage"] = 230.0
        self.transf_status["latency"] = 3.0
        self.lbl_prot_status.configure(text="Relés ANSI (50/51, 21, 87T, 67, 25, 59): OPERANDO DENTRO DOS LIMITES", text_color="#00ff00")
        status_txt = "ESTADO: NORMAL (GOOSE ATIVO | VM Bridge Npcap)" if SCAPY_AVAILABLE else "ESTADO: NORMAL (GOOSE ATIVO | 230kV)"
        self.status_box.configure(text=status_txt, text_color="#00ff00")
        self.draw_mimic_diagram()
        self.update_control_buttons_visual()

        self.enviar_pacote_goose(evento="RESTABELECIMENTO_PATIO", status="NORMAL")

        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        msg = f"[{timestamp}] INFO: Subestação de 230kV restabelecida aos parâmetros nominais.\n"
        self.log_alarm(msg)
        self.log_soe(msg)

    def log_alarm(self, text):
        self.alarm_box.configure(state="normal")
        self.alarm_box.insert("end", text)
        self.alarm_box.see("end")
        self.alarm_box.configure(state="disabled")

    def log_soe(self, text):
        self.soe_box.configure(state="normal")
        self.soe_box.insert("end", text)
        self.soe_box.see("end")
        self.soe_box.configure(state="disabled")

    def log_prot(self, text):
        self.prot_log_box.configure(state="normal")
        self.prot_log_box.insert("end", text)
        self.prot_log_box.see("end")
        self.prot_log_box.configure(state="disabled")

    def ack_alarms(self):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_alarm(f"[{timestamp}] OPERADOR: Alarmes reconhecidos (ACK).\n")

    def toggle_pause_trend(self):
        self.is_paused = not self.is_paused
        if self.is_paused:
            self.btn_pause_trend.configure(text="Retomar Gráfico", fg_color="#2980b9")
            self.log_alarm("[ANÁLISE SAGE] Gráfico congelado. Use os botões '◀ Histórico / Histórico ▶' ou a barra de ferramentas inferior para navegar livremente no tempo.\n")
        else:
            self.view_start = self.buffer_size - self.window_size
            self.ax1.set_xlim(self.view_start, self.view_start + self.window_size)
            self.btn_pause_trend.configure(text="Pausar / Analisar", fg_color="#1f6aa5")
            self.log_alarm("[ANÁLISE SAGE] Gráfico retomado em tempo real.\n")

    def update_telemetry(self):
        if str(self.tabview.get()) == "Diagrama Unifilar":
            self.draw_mimic_diagram()

        if self.is_bay_energized():
            curr_val = self.transf_status["current"] + random.uniform(-2, 2)
            volt_val = self.transf_status["voltage"] + random.uniform(-0.05, 0.05)
        else:
            curr_val = 0.0
            volt_val = 0.0

        self.y_curr.pop(0)
        self.y_curr.append(curr_val)

        self.y_volt.pop(0)
        self.y_volt.append(volt_val)

        if not self.is_paused:
            self.view_start = self.buffer_size - self.window_size
            self.ax1.set_xlim(self.view_start, self.view_start + self.window_size)

            self.line1.set_ydata(self.y_curr)
            self.line2.set_ydata(self.y_volt)

            self.ax1.relim()
            self.ax1.autoscale_view()
            self.ax2.relim()
            self.ax2.autoscale_view()

            self.canvas.draw()

        self.after(1000, self.update_telemetry)


if __name__ == "__main__":
    app = SubstationSCADAApp()
    app.mainloop()