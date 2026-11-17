# -*- coding: utf-8 -*-
"""
Sistema Supervisório para simulação SCADA 230kV (Diário de um Comissionador - Ep. 08)
Copyright (C) 2026 Patrick Araujo Santana

Este arquivo é parte do projeto scada-subestacao-230kv.
Este programa é um software livre: você pode redistribuí-lo e/ou modificá-lo
sob os termos da Licença Pública Geral GNU (GPLv3) conforme publicada pela
Free Software Foundation.
"""
import tkinter as tk
from tkinter import ttk
import customtkinter as ctk
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
import random
from datetime import datetime

# Configuração do Tema Global do CustomTkinter
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class SubstationSCADAApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Sistema SCADA / IEC 61850 - Subestação 230kV (Supervisório Completo)")
        self.geometry("1366x768")
        self.minsize(1024, 700)

        # Estados do Pátio: Disjuntores e Seccionadoras com Intertravamento Físico Rigoroso
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
            "latency": 3.0
        }

        self.is_paused = False
        self.animation_toggle = False

        # Buffer histórico estilo SAGE para o Gráfico de Tendências (120 amostras)
        self.buffer_size = 120
        self.x_data = list(range(self.buffer_size))
        self.y_curr = [180.0 + random.uniform(-5, 5) for _ in range(self.buffer_size)]
        self.y_volt = [230.0 + random.uniform(-0.5, 0.5) for _ in range(self.buffer_size)]

        self.window_size = 30
        self.view_start = self.buffer_size - self.window_size

        self.cursor_x1 = self.buffer_size - 20
        self.cursor_x2 = self.buffer_size - 10

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.create_header()
        self.create_tabview()
        self.update_telemetry()

    def create_header(self):
        header_frame = ctk.CTkFrame(self, height=50, corner_radius=0, fg_color=("#1f1f1f", "#141414"))
        header_frame.grid(row=0, column=0, sticky="ew")
        header_frame.grid_columnconfigure(1, weight=1)

        title_label = ctk.CTkLabel(header_frame, text="⚡ SUPERVISÓRIO SCADA 230kV — IEC 61850", font=ctk.CTkFont(size=16, weight="bold"))
        title_label.grid(row=0, column=0, padx=20, pady=10, sticky="w")

        self.status_box = ctk.CTkLabel(header_frame, text="ESTADO: NORMAL (GOOSE ATIVO | Latência Média: 3.2ms)", text_color="#2ecc71", font=ctk.CTkFont(size=11, weight="bold"))
        self.status_box.grid(row=0, column=2, padx=20, pady=10, sticky="e")

    def create_tabview(self):
        self.tabview = ctk.CTkTabview(self, fg_color="#1a1a1a")
        self.tabview.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)

        self.tab_mimic = self.tabview.add("Diagrama Unifilar")
        self.tab_control = self.tabview.add("Telecomando & Inter-Bays")
        self.tab_prot = self.tabview.add("Proteção ANSI & TR1")
        self.tab_alarms = self.tabview.add("Painel de Alarmes")
        self.tab_trends = self.tabview.add("Gráficos de Tendência Multi-Variável")
        self.tab_nodes = self.tabview.add("Nodos IEC 61850")
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

        title = ctk.CTkLabel(tab, text="Mimic Diagram - Pátio 230kV (Topologia Validada com Bypass, TR1 e Terra 89T1)", font=ctk.CTkFont(size=14, weight="bold"))
        title.grid(row=0, column=0, pady=10)

        self.canvas_mimic = tk.Canvas(tab, bg="#111111", highlightthickness=0)
        self.canvas_mimic.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
        self.canvas_mimic.bind("<Configure>", lambda e: self.draw_mimic_diagram())

        btn_frame = ctk.CTkFrame(tab, fg_color="transparent")
        btn_frame.grid(row=2, column=0, pady=10)

        ctk.CTkButton(btn_frame, text="Atualizar Desenho", fg_color="#2980b9", command=self.draw_mimic_diagram).pack(side="left", padx=5)
        ctk.CTkButton(btn_frame, text="Restabelecer Subestação", fg_color="#16a085", command=self.reset_patio).pack(side="left", padx=5)
        ctk.CTkButton(btn_frame, text="Simular Discrepância de Polos (52L1)", fg_color="#d35400", command=self.trigger_pole_discrepancy).pack(side="left", padx=5)

    def is_bay_energized(self):
        """Função unificada de validação topológica de energização do bay/transformador"""
        b1_closed = self.bay_status["52L1"]["closed"]
        b2_closed = self.bay_status["52L2"]["closed"]
        bp_closed = self.bay_status["89BP"]["closed"]
        # O bay/TR1 só energiza se 52L1 estiver fechado OU se o bypass estiver fechado COM o disjuntor de transferência 52L2 fechado
        return b1_closed or (bp_closed and b2_closed)

    def draw_mimic_diagram(self):
        c = self.canvas_mimic
        c.delete("all")
        width = c.winfo_width()
        height = c.winfo_height()
        if width < 100: width = 900
        if height < 100: height = 500

        self.animation_toggle = not self.animation_toggle
        b1_closed = self.bay_status["52L1"]["closed"]
        b2_closed = self.bay_status["52L2"]["closed"]
        bp_closed = self.bay_status["89BP"]["closed"]
        b3_closed = self.bay_status["89T1"]["closed"]

        energized_color = "#f1c40f" if self.animation_toggle else "#d4ac0d"
        deenergized_color = "#444444"

        # 1. Barramento Principal 230kV (Linha Superior)
        c.create_line(width * 0.10, height * 0.08, width * 0.90, height * 0.08, fill=energized_color, width=5)
        c.create_text(width * 0.5, height * 0.03, text="BARRAMENTO PRINCIPAL 230kV [ENERGIZADO]", fill="#f1c40f", font=("Consolas", 10, "bold"))

        # 2. Barramento de Transferência 230kV
        transf_bar_active = b2_closed
        transf_bar_color = energized_color if transf_bar_active else deenergized_color
        transf_bar_text_status = "ENERGIZADO" if transf_bar_active else "DESENERGIZADO"
        transf_bar_text_color = "#f1c40f" if transf_bar_active else "#e74c3c"

        c.create_line(width * 0.10, height * 0.22, width * 0.90, height * 0.22, fill=transf_bar_color, width=3, dash=(4, 2))
        c.create_text(width * 0.5, height * 0.17, text=f"BARRAMENTO DE TRANSFERÊNCIA 230kV [{transf_bar_text_status}]", fill=transf_bar_text_color, font=("Consolas", 9, "bold"))

        line1_color = energized_color if b1_closed else deenergized_color
        line2_color = energized_color if b2_closed else deenergized_color

        c.create_line(width * 0.35, height * 0.08, width * 0.35, height * 0.32, fill=line1_color, width=3)
        c.create_line(width * 0.65, height * 0.22, width * 0.65, height * 0.32, fill=line2_color, width=3)

        # Disjuntor 52L1
        b1_disc = self.bay_status["52L1"]["discrepancy"]
        b1_color = "#e74c3c" if b1_disc else ("#27ae60" if b1_closed else "#c0392b")
        b1_text = "52L1 (DISCR.)" if b1_disc else ("52L1 (FECH.)" if b1_closed else "52L1 (ABERTO)")
        c.create_rectangle(width * 0.29, height * 0.32, width * 0.41, height * 0.42, fill=b1_color, outline="white", width=2)
        c.create_text(width * 0.35, height * 0.37, text=b1_text, fill="white", font=("Arial", 8, "bold"))

        # Disjuntor 52L2 (Transferência)
        b2_color = "#27ae60" if b2_closed else "#c0392b"
        b2_text = "52L2 (FECH.)" if b2_closed else "52L2 (ABERTO)"
        c.create_rectangle(width * 0.59, height * 0.32, width * 0.71, height * 0.42, fill=b2_color, outline="white", width=2)
        c.create_text(width * 0.65, height * 0.37, text=b2_text, fill="white", font=("Arial", 8, "bold"))

        # Seccionadora de Bypass (89BP)
        bp_color = "#27ae60" if bp_closed else "#c0392b"
        bp_text = "89BP (FECHADA)" if bp_closed else "89BP (ABERTA)"
        c.create_rectangle(width * 0.16, height * 0.35, width * 0.27, height * 0.41, fill="#1f1f1f", outline=bp_color, width=2)
        c.create_text(width * 0.215, height * 0.38, text=bp_text, fill="white", font=("Arial", 7, "bold"))

        # O ramal do bypass só ganha cor energizada se o barramento de transferência estiver ativo E o bypass fechado
        bp_line_color = energized_color if (bp_closed and transf_bar_active) else deenergized_color
        c.create_line(width * 0.65, height * 0.22, width * 0.215, height * 0.22, fill=bp_line_color, width=2, dash=(2,2))
        c.create_line(width * 0.215, height * 0.22, width * 0.215, height * 0.38, fill=bp_line_color, width=2, dash=(2,2))
        c.create_line(width * 0.215, height * 0.38, width * 0.29, height * 0.38, fill=bp_line_color, width=2, dash=(2,2))

        c.create_line(width * 0.35, height * 0.42, width * 0.35, height * 0.50, fill=line1_color, width=3)
        c.create_line(width * 0.65, height * 0.42, width * 0.65, height * 0.50, fill=line2_color, width=3)
        
        # Validação topológica rigorosa do barramento inferior
        bus_inf_active = self.is_bay_energized()
        bus_inf_color = energized_color if bus_inf_active else deenergized_color
        
        c.create_line(width * 0.35, height * 0.50, width * 0.65, height * 0.50, fill=bus_inf_color, width=3)
        c.create_line(width * 0.5, height * 0.50, width * 0.5, height * 0.72, fill=bus_inf_color, width=3)

        # Seccionadora Terra (89T1)
        c.create_line(width * 0.5, height * 0.60, width * 0.60, height * 0.60, fill="#7f8c8d", width=2)
        
        box_color = "#e74c3c" if b3_closed else "#27ae60"
        box_text = "89T1 (TERRA FECHADA)" if b3_closed else "89T1 (TERRA ABERTA)"
        c.create_rectangle(width * 0.60, height * 0.56, width * 0.78, height * 0.64, fill="#1f1f1f", outline=box_color, width=2)
        c.create_text(width * 0.69, height * 0.60, text=box_text, fill="white", font=("Arial", 9, "bold"))

        # Símbolo de Aterramento IEC
        c.create_line(width * 0.69, height * 0.64, width * 0.69, height * 0.70, fill="#bdc3c7", width=2)
        c.create_line(width * 0.65, height * 0.70, width * 0.73, height * 0.70, fill="#bdc3c7", width=2)
        c.create_line(width * 0.66, height * 0.72, width * 0.72, height * 0.72, fill="#bdc3c7", width=2)
        c.create_line(width * 0.67, height * 0.74, width * 0.71, height * 0.74, fill="#bdc3c7", width=1)

        # Transformador TR1
        tr_color = energized_color if bus_inf_active else deenergized_color
        tr_text_status = "ENERGIZADO" if bus_inf_active else "DESENERGIZADO"
        current_val = self.transf_status['current'] if bus_inf_active else 0.0
        
        c.create_oval(width * 0.46, height * 0.72, width * 0.54, height * 0.81, outline=tr_color, width=2)
        c.create_oval(width * 0.46, height * 0.81, width * 0.54, height * 0.90, outline=tr_color, width=2)
        
        c.create_text(width * 0.5, height * 0.94, text=f"TR1 (230/13.8kV) [{tr_text_status}] | Temp: {self.transf_status['temp']}°C | Corr: {current_val}A | Latência: {self.transf_status['latency']}ms", fill="#f1c40f" if bus_inf_active else "#e74c3c", font=("Consolas", 9, "bold"))

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
        for bay_id, btns in self.control_buttons.items():
            is_closed = self.bay_status[bay_id]["closed"]
            if is_closed:
                btns["abrir"].configure(fg_color="#c0392b", text_color="white", border_width=2, border_color="#f1c40f")
                btns["fechar"].configure(fg_color="#555555", text_color="#aaaaaa", border_width=0)
            else:
                btns["fechar"].configure(fg_color="#27ae60", text_color="white", border_width=2, border_color="#f1c40f")
                btns["abrir"].configure(fg_color="#555555", text_color="#aaaaaa", border_width=0)

    def build_protection_tab(self):
        tab = self.tab_prot
        tab.grid_rowconfigure(2, weight=1)
        tab.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(tab, text="Módulo de Proteção ANSI Completa - Funções Dedicadas com Atuação no Pátio", font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, pady=10)
        
        p_frame = ctk.CTkFrame(tab, fg_color="#1f1f1f")
        p_frame.grid(row=1, column=0, sticky="ew", padx=20, pady=5)

        self.lbl_prot_status = ctk.CTkLabel(p_frame, text="Relés ANSI (50/51, 21, 87T, 67, 25, 59): OPERANDO DENTRO DOS LIMITES", font=ctk.CTkFont(size=11, family="Consolas"), text_color="#2ecc71")
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
        self.v2 = self.ax1.axvline(x=self.cursor_x2, color='#2ecc71', linestyle='--', linewidth=2, label='Pena 2 (Fim Falta)')

        self.ax1.tick_params(colors='white', labelsize=8)
        self.ax2.tick_params(colors='white', labelsize=8)
        self.ax1.xaxis.label.set_color('white')
        for spine in self.ax1.spines.values(): spine.set_color('#555555')
        for spine in self.ax2.spines.values(): spine.set_color('#555555')

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

        ctk.CTkLabel(tab, text="Árvore de Nodos Lógicos IEC 61850 (Subestação 230kV)", font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, pady=10)

        tree_box = ctk.CTkTextbox(tab, font=ctk.CTkFont(family="Consolas", size=11), fg_color="#111111")
        tree_box.grid(row=1, column=0, sticky="nsew", padx=15, pady=10)
        
        tree_text = (
            "📁 E230kV_Substation\n"
            " └─ 📁 Bay_L1 (230kV)\n"
            "     ├─ 📄 XCBR1.Pos.stVal (Disjuntor Tripolar A/B/C)\n"
            "     ├─ 📄 CSWI1.Pos.ctlModel (Controle Operacional / CILO Interlocks)\n"
            "     ├─ 📄 MMXU1.A.phsA/B/C (Medição Fasorial de Corrente e Tensão)\n"
            "     └─ 🌐 GOOSE Dataset (Latência de Publication: ~3.1ms)\n"
            " └─ 📁 Bay_TR1 (230/13.8kV)\n"
            "     ├─ 📄 PTOC1, PDIS1, RREC1, TTRC1 (Funções ANSI)\n"
            "     └─ 🌐 GOOSE Trip Command (Latência de Rede: ~2.1ms)\n"
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

    def execute_command(self, bay_id, should_close):
        # 1. Regra de Intertravamento: Fechar seccionadora de terra (89T1) com circuito energizado
        if should_close and bay_id == "89T1":
            if self.is_bay_energized() or self.bay_status["89BP"]["closed"]:
                timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                msg = f"[{timestamp}] INTERTRAVAMENTO CILO: Bloqueio! Tentativa de fechar a Seccionadora Terra (89T1) com barramento/linha energizados.\n"
                self.log_alarm(msg)
                self.log_soe(msg)
                return

        # 2. Regra de Intertravamento: Fechar seccionadora de bypass (89BP) com barra de transferência morta
        if should_close and bay_id == "89BP":
            if not self.bay_status["52L2"]["closed"]:
                timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                msg = f"[{timestamp}] INTERTRAVAMENTO CILO: Bloqueio! Tentativa de fechar o Bypass (89BP) com o Barramento de Transferência desenergizado (52L2 aberto).\n"
                self.log_alarm(msg)
                self.log_soe(msg)
                return

        # 3. Regra de Intertravamento: Abrir seccionadora de bypass (89BP) sob carga sem retorno pelo disjuntor principal
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

        # Atualização unificada de corrente e tensão do transformador
        if self.is_bay_energized():
            self.transf_status["current"] = 180.0
            self.transf_status["voltage"] = 230.0
        else:
            self.transf_status["current"] = 0.0
            self.transf_status["voltage"] = 0.0

        self.draw_mimic_diagram()
        self.update_control_buttons_visual()
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
        self.lbl_prot_status.configure(text="Relés ANSI (50/51, 21, 87T, 67, 25, 59): OPERANDO DENTRO DOS LIMITES", text_color="#2ecc71")
        self.status_box.configure(text="ESTADO: NORMAL (GOOSE ATIVO | 230kV)", text_color="#2ecc71")
        self.draw_mimic_diagram()
        self.update_control_buttons_visual()
        
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
            curr_val = self.transf_status["current"] + random.uniform(-5, 5)
            volt_val = self.transf_status["voltage"] + random.uniform(-0.1, 0.1)
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