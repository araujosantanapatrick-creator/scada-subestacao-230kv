# -*- coding: utf-8 -*-
"""
Sistema Supervisório para simulação SCADA 230kV (Diário de um Comissionador)
Copyright (C) 2026 Patrick Araujo Santana

Este arquivo é parte do projeto scada-subestacao-230kv.
Este programa é um software livre: você pode redistribuí-lo e/ou modificá-lo
sob os termos da Licença Pública Geral GNU (GPLv3) conforme publicada pela
Free Software Foundation.
"""
import ctypes
from ctypes import wintypes
import json
import math
import os
import queue
import random
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime

import customtkinter as ctk
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

try:
    from scapy.all import Ether, Raw, conf, get_if_addr, get_if_hwaddr, get_if_list, sendp
    try:
        from scapy.all import sendpfast as _scapy_sendpfast
        SENDPFAST_AVAILABLE = callable(_scapy_sendpfast)
    except Exception:
        _scapy_sendpfast = None
        SENDPFAST_AVAILABLE = False
    SCAPY_AVAILABLE = True
except Exception:
    conf = None
    get_if_addr = None
    get_if_hwaddr = None
    get_if_list = None
    sendp = None
    _scapy_sendpfast = None
    SENDPFAST_AVAILABLE = False
    SCAPY_AVAILABLE = False

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

C_MMS_EXE_NAME = "iec61850_mms_server.exe"
C_MMS_EXE_DEFAULT = os.path.join(r"C:\SCADA_230kV", C_MMS_EXE_NAME)
UDP_BRIDGE_HOST = "127.0.0.1"
UDP_C_SERVER_PORT = 50102
UDP_PY_CMD_PORT = 50103
GOOSE_MULTICAST_MAC = "01:0c:cd:01:00:00"
SV_MULTICAST_MAC = "01:0c:cd:04:00:01"


def _iter_scapy_ifaces():
    if not SCAPY_AVAILABLE:
        return []
    entries = []
    seen = set()
    try:
        if conf is not None and getattr(conf, "ifaces", None):
            for key, iface_obj in conf.ifaces.items():
                dev = getattr(iface_obj, "network_name", None) or getattr(iface_obj, "npf_name", None) or str(key)
                desc = getattr(iface_obj, "description", None) or getattr(iface_obj, "name", None) or str(key)
                ip = getattr(iface_obj, "ip", None) or ""
                if dev and dev not in seen:
                    seen.add(dev)
                    entries.append((dev, str(desc), str(ip)))
    except Exception:
        pass
    try:
        for dev in (get_if_list() or []):
            if dev and dev not in seen:
                seen.add(dev)
                entries.append((dev, str(dev), ""))
    except Exception:
        pass
    return entries


def _resolve_scapy_iface(iface_name):
    if not SCAPY_AVAILABLE:
        return iface_name
    nome = str(iface_name or "").strip()
    if not nome:
        return nome
    entries = _iter_scapy_ifaces()
    for dev, desc, _ip in entries:
        if nome == dev or nome.lower() == desc.lower():
            return dev
    low = nome.lower()
    for dev, desc, _ip in entries:
        combined = f"{dev} {desc}".lower()
        if low in combined:
            return dev
        if "vmnet8" in low and "vmnet8" in combined:
            return dev
        if "vmnet1" in low and "vmnet1" in combined:
            return dev
        if low in ("wi-fi", "wifi") and ("wi-fi" in combined or "wlan" in combined or "wireless" in combined):
            return dev
    return nome


def resolve_c_mms_exe_path() -> str:
    candidates = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            if hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(meipass)
                except Exception:
                    pass
            candidates.append(os.path.join(meipass, C_MMS_EXE_NAME))
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        if exe_dir and hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(exe_dir)
            except Exception:
                pass
        candidates.append(os.path.join(exe_dir, C_MMS_EXE_NAME))
    candidates.append(C_MMS_EXE_DEFAULT)
    script_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
    candidates.append(os.path.join(script_dir, C_MMS_EXE_NAME))

    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return C_MMS_EXE_DEFAULT


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

        self.state_lock = threading.Lock()
        self.running = True
        self._main_thread_id = threading.get_ident()
        self._protocol_error_queue = queue.Queue()
        self._last_protocol_error = {}

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

        self.freq_hz = 60.0
        self.freq_override_active = False
        self.freq_target_hz = 60.0
        self.phase_angle = 0.0

        self.prot_81 = {
            "81U_enabled": True,
            "81U_pickup": 59.50,
            "81U_delay": 0.50,
            "81U_timer": 0.0,
            "81U_pickup_active": False,
            "81U_tripped": False,
            "81O_enabled": True,
            "81O_pickup": 60.50,
            "81O_delay": 0.50,
            "81O_timer": 0.0,
            "81O_pickup_active": False,
            "81O_tripped": False,
        }

        self.goose_lock = threading.Lock()
        self.goose_st_num = 1
        self.goose_sq_num = 0
        self.goose_event_id = "ESTADO_INICIAL"
        self.goose_val_bool = True
        self.goose_last_change_ts = time.time()

        self.sv_lock = threading.Lock()
        self.sv_running = False
        self.sv_smp_cnt = 0
        self.sv_id = "SV_TR1_230KV"

        self.mms_lock = threading.Lock()
        self.mms_running = False
        self.mms_port_active = None
        self.mms_bind_ip = None
        self.mms_last_error = ""
        self._mms_sock = None
        self._restarting_mms = False
        self._c_mms_proc = None
        self._c_mms_log_thread = None
        self._udp_tx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._udp_rx_sock = None
        self._init_udp_cmd_listener()

        self.is_paused = False
        self.animation_toggle = False

        self.buffer_size = 120
        self.x_data = list(range(self.buffer_size))
        self.y_curr = [180.0 + random.uniform(-5, 5) for _ in range(self.buffer_size)]
        self.y_volt = [230.0 + random.uniform(-0.5, 0.5) for _ in range(self.buffer_size)]
        self.y_freq = [60.0 + random.uniform(-0.02, 0.02) for _ in range(self.buffer_size)]

        self.window_size = 30
        self.view_start = self.buffer_size - self.window_size

        self.cursor_x1 = self.buffer_size - 20
        self.cursor_x2 = self.buffer_size - 10

        interfaces_padrao = ["Wi-Fi", "VMnet8", "VMnet1", "Ethernet", "Ethernet 0"]
        if SCAPY_AVAILABLE:
            try:
                scapy_labels = []
                for _dev, desc, _ip in _iter_scapy_ifaces():
                    if desc and desc not in scapy_labels:
                        scapy_labels.append(desc)
                self.lista_interfaces = list(dict.fromkeys(interfaces_padrao + scapy_labels))
            except Exception:
                self.lista_interfaces = interfaces_padrao
        else:
            self.lista_interfaces = interfaces_padrao

        default_iface = "Wi-Fi" if "Wi-Fi" in self.lista_interfaces else self.lista_interfaces[0]
        self.interface_selecionada = ctk.StringVar(value=default_iface)
        self._selected_iface_name = default_iface

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.create_header()
        self.create_tabview()

        self.after(200, self._deferred_startup)
        self.after(1000, self.update_telemetry)

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _deferred_startup(self):
        if not self.running:
            return
        threading.Thread(target=self.start_mms_server, daemon=True).start()

        self.thread_goose_hb = threading.Thread(target=self._loop_heartbeat_goose, daemon=True)
        self.thread_goose_hb.start()

        self.thread_sv = threading.Thread(target=self._loop_sampled_values, daemon=True)
        self.thread_sv.start()

        self.thread_udp_rx = threading.Thread(target=self._loop_udp_commands, daemon=True)
        self.thread_udp_rx.start()

    def _ui_call(self, callback, *args):
        if not self.running:
            return
        if threading.get_ident() == self._main_thread_id:
            callback(*args)
        else:
            self._protocol_error_queue.put(("__UI_CALLBACK__", (callback, args)))

    def _report_protocol_error(self, protocol, exc):
        message = f"{type(exc).__name__}: {exc}"
        if self._last_protocol_error.get(protocol) != message:
            self._last_protocol_error[protocol] = message
            self._protocol_error_queue.put((protocol, message))

    def _drain_protocol_errors(self):
        while True:
            try:
                protocol, message = self._protocol_error_queue.get_nowait()
            except queue.Empty:
                break
            if protocol == "__UI_CALLBACK__":
                callback, args = message
                try:
                    if self.running:
                        callback(*args)
                except Exception as exc:
                    self._last_protocol_error["UI"] = f"{type(exc).__name__}: {exc}"
                continue
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            line = f"[{ts}] [{protocol}] ERRO: {message}\n"
            if hasattr(self, "alarm_box"):
                self.log_alarm(line)
            if hasattr(self, "soe_box"):
                self.log_soe(line)

    def _init_udp_cmd_listener(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(0.25)
            sock.bind((UDP_BRIDGE_HOST, UDP_PY_CMD_PORT))
            self._udp_rx_sock = sock
        except OSError as exc:
            self.mms_last_error = f"Falha ao abrir porta UDP {UDP_PY_CMD_PORT}: {exc}"
            try:
                sock.close()
            except OSError:
                pass
            self._udp_rx_sock = None

    def send_udp_state_to_c(self):
        with self.state_lock:
            payload = {
                "52L1": 2 if self.bay_status["52L1"]["closed"] else 1,
                "89BP": 1 if self.bay_status["89BP"]["closed"] else 0,
                "89T1": 1 if self.bay_status["89T1"]["closed"] else 0,
                "81U": 1 if self.prot_81["81U_tripped"] else 0,
                "81O": 1 if self.prot_81["81O_tripped"] else 0,
                "hz": round(float(self.freq_hz), 3),
                "kv": round(float(self.transf_status["voltage"]), 3),
                "amp": round(float(self.transf_status["current"]), 3),
            }
        try:
            raw = (json.dumps(payload) + "\n").encode("utf-8")
            self._udp_tx_sock.sendto(raw, (UDP_BRIDGE_HOST, UDP_C_SERVER_PORT))
        except OSError:
            pass

    def _apply_remote_operate_52l1(self, close_target: bool):
        if not self.running:
            return
        with self.state_lock:
            allowed, reason = self._check_cilo_interlock("52L1", close_target)
            if not allowed:
                ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                msg = f"[{ts}] [MMS/UDP] COMANDO 52L1 REJEITADO PELO CILO: {reason}\n"
                self.log_alarm(msg)
                self.log_soe(msg)
                self.send_udp_state_to_c()
                return

            self.bay_status["52L1"]["closed"] = close_target
            for p in self.bay_status["52L1"]["poles"]:
                self.bay_status["52L1"]["poles"][p] = close_target
            self.bay_status["52L1"]["discrepancy"] = False
            self._recalc_electrical_state()

        status_str = "FECHADO" if close_target else "ABERTO"
        self.draw_mimic_diagram()
        self.update_control_buttons_visual()
        self.refresh_nodes_tree()
        self.enviar_pacote_goose(evento="POS_52L1_MMS", status=status_str)
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        msg = f"[{ts}] [MMS/UDP] Comando remoto XCBR1.Pos -> 52L1 {status_str}.\n"
        self.log_alarm(msg)
        self.log_soe(msg)
        self.send_udp_state_to_c()

    def _loop_udp_commands(self):
        while self.running:
            sock = self._udp_rx_sock
            if sock is None:
                time.sleep(0.5)
                continue
            try:
                data, _ = sock.recvfrom(2048)
                if not data:
                    continue
                for raw_line in data.decode("utf-8", errors="ignore").splitlines():
                    line = raw_line.strip()
                    if not line:
                        continue
                    msg = json.loads(line)
                    if msg.get("cmd") == "OPERATE" and msg.get("bay") == "52L1":
                        raw_close = msg.get("close", msg.get("ctlVal", 1) == 2)
                        if isinstance(raw_close, bool):
                            close_target = raw_close
                        elif isinstance(raw_close, (int, float)):
                            close_target = int(raw_close) == 2
                        elif isinstance(raw_close, str):
                            close_target = raw_close.strip().lower() in ("true", "1", "2", "close", "closed", "fechado")
                        else:
                            raise ValueError(f"Valor de comando OPERATE inválido: {raw_close!r}")
                        self._ui_call(self._apply_remote_operate_52l1, close_target)
            except socket.timeout:
                continue
            except Exception as exc:
                if self.running:
                    self._report_protocol_error("UDP", exc)
                time.sleep(0.1)

    def on_close(self):
        self.running = False
        self.sv_running = False
        try:
            if self._udp_rx_sock is not None:
                self._udp_rx_sock.close()
        except OSError:
            pass
        try:
            self._udp_tx_sock.close()
        except OSError:
            pass
        self.stop_mms_server()
        self.destroy()

    def _get_windows_ipv4_map(self):
        ips = []
        if os.name == "nt":
            try:
                ws_inet = socket.AF_INET
                sio_get_interface_list = 0x4004747F

                class SOCKADDR_IN(ctypes.Structure):
                    _fields_ = [
                        ("sin_family", wintypes.USHORT),
                        ("sin_port", wintypes.USHORT),
                        ("sin_addr", wintypes.BYTE * 4),
                        ("sin_zero", wintypes.BYTE * 8),
                    ]

                class INTERFACE_INFO(ctypes.Structure):
                    _fields_ = [
                        ("iiFlags", wintypes.ULONG),
                        ("iiAddress", SOCKADDR_IN),
                        ("iiBroadcastAddress", SOCKADDR_IN),
                        ("iiNetmask", SOCKADDR_IN),
                    ]

                s = socket.socket(ws_inet, socket.SOCK_DGRAM)
                try:
                    in_buf = ctypes.c_ulong(0)
                    out_buf = (INTERFACE_INFO * 32)()
                    bytes_returned = ctypes.c_ulong(0)
                    rc = ctypes.windll.ws2_32.WSAIoctl(
                        s.fileno(),
                        sio_get_interface_list,
                        ctypes.byref(in_buf),
                        ctypes.sizeof(in_buf),
                        ctypes.byref(out_buf),
                        ctypes.sizeof(out_buf),
                        ctypes.byref(bytes_returned),
                        None,
                        None,
                    )
                    if rc == 0:
                        count = bytes_returned.value // ctypes.sizeof(INTERFACE_INFO)
                        for idx in range(min(count, 32)):
                            addr_bytes = bytes(out_buf[idx].iiAddress.sin_addr)
                            ip_str = socket.inet_ntoa(addr_bytes)
                            if ip_str and not ip_str.startswith("127.") and ip_str not in ips:
                                ips.append(ip_str)
                finally:
                    s.close()
            except Exception:
                pass

        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ip_str = info[4][0]
                if ip_str and not ip_str.startswith("127.") and ip_str not in ips:
                    ips.append(ip_str)
        except Exception:
            pass

        return ips

    def resolve_interface_ipv4(self, iface_name):
        nome = (iface_name or "").strip()
        nome_low = nome.lower()

        if SCAPY_AVAILABLE and nome:
            try:
                iface_real = _resolve_scapy_iface(nome)
                if conf is not None and getattr(conf, "ifaces", None):
                    iface_obj = conf.ifaces.get(iface_real) or conf.ifaces.get(nome)
                    if iface_obj is not None:
                        ip_obj = getattr(iface_obj, "ip", None)
                        if ip_obj and ip_obj != "0.0.0.0" and not str(ip_obj).startswith("127."):
                            return str(ip_obj)
                if callable(get_if_addr):
                    ip_iface = get_if_addr(iface_real)
                    if ip_iface and ip_iface != "0.0.0.0" and not str(ip_iface).startswith("127."):
                        return str(ip_iface)
            except Exception:
                pass

        ips_locais = self._get_windows_ipv4_map()
        if "vmnet8" in nome_low:
            return next((ip for ip in ips_locais if ip.startswith("192.168.199.")), "192.168.199.1")
        if "vmnet1" in nome_low:
            return next((ip for ip in ips_locais if ip.startswith("192.168.188.")), "192.168.188.1")
        if nome_low in ("wi-fi", "wifi") or "wi-fi" in nome_low:
            return next((ip for ip in ips_locais if ip.startswith("10.0.0.")), "10.0.0.85")

        if ips_locais:
            return ips_locais[0]
        return "127.0.0.1"

    def update_mms_badge(self):
        if not hasattr(self, "lbl_mms_status"):
            return

        iface = self.interface_selecionada.get().strip() or "N/A"
        ip_alvo = self.resolve_interface_ipv4(iface)

        with self.mms_lock:
            mms_on = self.mms_running
            porta = self.mms_port_active
            bind_ip = self.mms_bind_ip
            err = self.mms_last_error
            restarting = self._restarting_mms

        if restarting:
            self.lbl_mms_status.configure(
                text=f"MMS: RECONECTANDO | {iface} ({ip_alvo})...",
                text_color="#ffd600",
                fg_color="#2b2612"
            )
        elif mms_on and porta and bind_ip:
            modo = "C-LIB" if (self._c_mms_proc is not None and self._c_mms_proc.poll() is None) else "PY"
            self.lbl_mms_status.configure(
                text=f"MMS ({modo}): ATIVO | {iface} ({bind_ip}:{porta})",
                text_color="#00e676",
                fg_color="#122b1d"
            )
        elif err:
            self.lbl_mms_status.configure(
                text=f"MMS: ERRO | {iface} ({ip_alvo}) — {err}",
                text_color="#ff5252",
                fg_color="#2e1518"
            )
        else:
            self.lbl_mms_status.configure(
                text=f"MMS: PARADO | {iface} ({ip_alvo})",
                text_color="#ffb74d",
                fg_color="#2b2112"
            )

    def on_interface_changed(self, nova_placa):
        placa_limpa = (nova_placa or "").strip()
        self.interface_selecionada.set(placa_limpa)
        self._selected_iface_name = placa_limpa
        ip_novo = self.resolve_interface_ipv4(placa_limpa)

        with self.mms_lock:
            was_mms_on = self.mms_running
            if was_mms_on:
                self._restarting_mms = True

        if was_mms_on:
            self.update_mms_badge()
            threading.Thread(
                target=self._restart_mms_worker,
                args=(placa_limpa, ip_novo),
                daemon=True
            ).start()
        else:
            self.update_mms_badge()
            self.refresh_nodes_tree()
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            self.log_soe(f"[{ts}] [REDE] Placa alterada para '{placa_limpa}' (IPv4: {ip_novo}) | MMS: PARADO.\n")

    def _restart_mms_worker(self, placa_limpa, ip_novo):
        try:
            self.stop_mms_server()
            self.start_mms_server(preferred_ip=ip_novo)
        finally:
            with self.mms_lock:
                self._restarting_mms = False
            if self.running:
                self._ui_call(self._after_interface_restart, placa_limpa, ip_novo)

    def _after_interface_restart(self, placa_limpa, ip_novo):
        self.update_mms_badge()
        self.refresh_nodes_tree()
        if hasattr(self, "btn_mms_toggle"):
            if self.mms_running:
                self.btn_mms_toggle.configure(text="MMS: ON", fg_color="#1b8a4b", hover_color="#146c3a")
            else:
                self.btn_mms_toggle.configure(text="MMS: OFF", fg_color="#37474f", hover_color="#455a64")
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        status_mms = f"{self.mms_bind_ip}:{self.mms_port_active}" if (self.mms_running and self.mms_port_active) else "PARADO"
        self.log_soe(f"[{ts}] [REDE] Placa alterada para '{placa_limpa}' (IPv4: {ip_novo}) | MMS: {status_mms}.\n")

    def create_header(self):
        header_frame = ctk.CTkFrame(self, height=55, corner_radius=0, fg_color=("#1f1f1f", "#141414"))
        header_frame.grid(row=0, column=0, sticky="ew")
        header_frame.grid_columnconfigure(4, weight=1)

        title_label = ctk.CTkLabel(header_frame, text="⚡ SUPERVISÓRIO SCADA 230kV — IEC 61850", font=ctk.CTkFont(size=15, weight="bold"))
        title_label.grid(row=0, column=0, padx=12, pady=10, sticky="w")

        iface_frame = ctk.CTkFrame(header_frame, fg_color="transparent")
        iface_frame.grid(row=0, column=1, padx=6, pady=5)

        lbl_iface = ctk.CTkLabel(iface_frame, text="Placa Rede:", font=ctk.CTkFont(size=11, weight="bold"), text_color="#3498db")
        lbl_iface.pack(side="left", padx=2)

        self.menu_iface = ctk.CTkComboBox(
            iface_frame,
            values=self.lista_interfaces,
            variable=self.interface_selecionada,
            width=145,
            height=28,
            font=ctk.CTkFont(size=11),
            command=self.on_interface_changed
        )
        self.menu_iface.pack(side="left", padx=2)

        self.lbl_mms_status = ctk.CTkLabel(
            header_frame,
            text="MMS: INICIANDO...",
            font=ctk.CTkFont(size=11, family="Consolas", weight="bold"),
            text_color="#00e676",
            fg_color="#122b1d",
            corner_radius=6,
            padx=10,
            pady=4
        )
        self.lbl_mms_status.grid(row=0, column=2, padx=6, pady=8)

        proto_frame = ctk.CTkFrame(header_frame, fg_color="transparent")
        proto_frame.grid(row=0, column=3, padx=6, pady=5)

        self.btn_sv_toggle = ctk.CTkButton(
            proto_frame,
            text="SV: OFF",
            width=74,
            height=28,
            fg_color="#37474f",
            hover_color="#455a64",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self.toggle_sv
        )
        self.btn_sv_toggle.pack(side="left", padx=3)

        self.btn_mms_toggle = ctk.CTkButton(
            proto_frame,
            text="MMS: ON",
            width=82,
            height=28,
            fg_color="#1b8a4b",
            hover_color="#146c3a",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self.toggle_mms
        )
        self.btn_mms_toggle.pack(side="left", padx=3)

        status_txt = "ESTADO: NORMAL (GOOSE + MMS | Npcap)" if SCAPY_AVAILABLE else "ESTADO: NORMAL (Simulação + MMS)"
        self.status_box = ctk.CTkLabel(
            header_frame,
            text=status_txt,
            text_color="#00ff00" if SCAPY_AVAILABLE else "#f39c12",
            font=ctk.CTkFont(size=11, weight="bold")
        )
        self.status_box.grid(row=0, column=4, padx=12, pady=10, sticky="e")

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

    # =========================================================================
    # TOPOLOGIA DO UNIFILAR (MIMIC DIAGRAM) & INTERTRAVAMENTOS (CILO)
    # =========================================================================
    def is_transfer_bus_energized(self):
        return bool(self.bay_status["52L2"]["closed"] and not self.bay_status["52L2"]["discrepancy"])

    def is_bay_energized(self):
        b1_ok = self.bay_status["52L1"]["closed"] and not self.bay_status["52L1"]["discrepancy"]
        b2_ok = self.is_transfer_bus_energized()
        bp_ok = self.bay_status["89BP"]["closed"] and not self.bay_status["89BP"]["discrepancy"]
        return bool(b1_ok or (bp_ok and b2_ok))

    def has_any_voltage_reference(self):
        return bool(self.is_bay_energized() or self.is_transfer_bus_energized())

    def _check_cilo_interlock(self, bay_id, should_close):
        b1 = self.bay_status["52L1"]
        b2 = self.bay_status["52L2"]
        bp = self.bay_status["89BP"]
        t1 = self.bay_status["89T1"]

        if bay_id == "52L1" and b1["discrepancy"] and should_close:
            return False, "Disjuntor 52L1 em discrepância de polos: abra e inspecione antes de fechar."

        if should_close and bay_id == "89T1":
            if self.is_bay_energized():
                return False, "Bay/TR1 energizado (230kV): bloqueio de fechamento da Seccionadora Terra (89T1)."
            if self.is_transfer_bus_energized() and bp["closed"]:
                return False, "Barramento de transferência energizado com 89BP fechado: bloqueio em 89T1."
            if bp["closed"]:
                return False, "Seccionadora de Bypass (89BP) fechada: bloqueio em 89T1."
            if b1["closed"] or b2["closed"]:
                return False, "Disjuntores 52L1/52L2 fechados: abra os disjuntores antes de fechar 89T1."

        if should_close and bay_id == "52L2":
            if t1["closed"]:
                return False, "Seccionadora Terra (89T1) fechada: bloqueio de fechamento do Disjuntor 52L2."

        if should_close and bay_id == "52L1":
            if t1["closed"]:
                return False, "Seccionadora Terra (89T1) fechada: bloqueio de fechamento do Disjuntor 52L1."
            if bp["closed"] and b2["closed"]:
                return False, "Transferência via 89BP + 52L2 já ativa: evite fechamento paralelo indevido de 52L1."

        if should_close and bay_id == "89BP":
            if t1["closed"]:
                return False, "Seccionadora Terra (89T1) fechada: bloqueio de fechamento do Bypass (89BP)."
            if not b2["closed"]:
                return False, "Barramento de transferência desenergizado (52L2 aberto): bloqueio em 89BP."

        if not should_close and bay_id == "89BP":
            if self.is_bay_energized() and not b1["closed"]:
                return False, "Tentativa de abrir 89BP conduzindo corrente de transferência sem 52L1 fechado."

        if not should_close and bay_id == "52L2":
            if bp["closed"] and not b1["closed"] and self.is_bay_energized():
                return False, "Abertura de 52L2 sob carga de transferência sem 52L1 fechado: bloqueio CILO."

        return True, ""

    def _recalc_electrical_state(self):
        energized = self.is_bay_energized()
        if energized:
            if self.transf_status["current"] <= 0.0:
                self.transf_status["current"] = 180.0
            if self.transf_status["voltage"] <= 0.0:
                self.transf_status["voltage"] = 230.0
        else:
            self.transf_status["current"] = 0.0
            self.transf_status["voltage"] = 0.0

        self.bay_status["52L1"]["current"] = self.transf_status["current"] if self.bay_status["52L1"]["closed"] else 0.0
        self.bay_status["52L2"]["current"] = self.transf_status["current"] if (self.bay_status["52L2"]["closed"] and self.bay_status["89BP"]["closed"]) else 0.0

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
        with self.state_lock:
            b1_closed = self.bay_status["52L1"]["closed"]
            b2_closed = self.bay_status["52L2"]["closed"]
            bp_closed = self.bay_status["89BP"]["closed"]
            b3_closed = self.bay_status["89T1"]["closed"]
            b1_disc = self.bay_status["52L1"]["discrepancy"]
            transf_bar_active = self.is_transfer_bus_energized()
            bus_inf_active = self.is_bay_energized()
            tr_voltage = self.transf_status["voltage"]
            tr_tap = self.transf_status["tap"]
            tr_temp = self.transf_status["temp"]
            tr_latency = self.transf_status["latency"]
            current_val = self.transf_status["current"] if bus_inf_active else 0.0
            freq_val = self.freq_hz
            any_81_trip = bool(self.prot_81["81U_tripped"] or self.prot_81["81O_tripped"])

        energized_green = "#00ff00"
        mv_color = "#ffff00"
        deenergized_color = "#455a64"

        c.create_line(width * 0.10, height * 0.09, width * 0.90, height * 0.09, fill=energized_green, width=5)
        c.create_text(width * 0.5, height * 0.04, text="BARRAMENTO PRINCIPAL 230kV [ENERGIZADO]", fill=energized_green, font=("Consolas", 10, "bold"))

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

        self._draw_breaker_52(c, width * 0.35, height * 0.36, b_size, b1_closed, "52L1", energized_green, discrepancy=b1_disc)
        self._draw_breaker_52(c, width * 0.65, height * 0.36, b_size, b2_closed, "52L2", energized_green, discrepancy=False)

        bp_line_color = energized_green if (bp_closed and transf_bar_active) else deenergized_color
        c.create_line(width * 0.65, height * 0.23, width * 0.22, height * 0.23, fill=bp_line_color, width=2, dash=(3, 2))
        self._draw_disconnector_89(c, width * 0.22, height * 0.23, width * 0.22, height * 0.36, bp_closed, bp_line_color, motorized=False)
        c.create_line(width * 0.22, height * 0.36, width * 0.35 - b_size / 2, height * 0.36, fill=bp_line_color, width=2, dash=(3, 2))
        c.create_text(width * 0.16, height * 0.30, text=f"89BP ({'FECH.' if bp_closed else 'AB.'})", fill="#ffffff", font=("Consolas", 8, "bold"))

        c.create_line(width * 0.35, height * 0.36 + b_size / 2, width * 0.35, height * 0.50, fill=line1_color, width=3)
        c.create_line(width * 0.65, height * 0.36 + b_size / 2, width * 0.65, height * 0.50, fill=line2_color, width=3)

        bus_inf_color = energized_green if bus_inf_active else deenergized_color

        c.create_line(width * 0.35, height * 0.50, width * 0.65, height * 0.50, fill=bus_inf_color, width=3)
        c.create_line(width * 0.50, height * 0.50, width * 0.50, height * 0.72, fill=bus_inf_color, width=3)

        c.create_line(width * 0.50, height * 0.60, width * 0.61, height * 0.60, fill=bus_inf_color, width=2)
        t1_line_color = "#ff5252" if b3_closed else deenergized_color
        self._draw_disconnector_89(c, width * 0.61, height * 0.60, width * 0.71, height * 0.60, b3_closed, t1_line_color, motorized=False)
        c.create_text(width * 0.66, height * 0.56, text=f"89T1 ({'TERRA' if b3_closed else 'AB.'})", fill="#ff5252" if b3_closed else "#ffffff", font=("Consolas", 8, "bold"))

        c.create_line(width * 0.71, height * 0.60, width * 0.71, height * 0.69, fill="#cfd8dc", width=2)
        c.create_line(width * 0.67, height * 0.69, width * 0.75, height * 0.69, fill="#cfd8dc", width=2)
        c.create_line(width * 0.685, height * 0.71, width * 0.735, height * 0.71, fill="#cfd8dc", width=2)
        c.create_line(width * 0.70, height * 0.73, width * 0.72, height * 0.73, fill="#cfd8dc", width=1.5)

        tr_color_top = energized_green if bus_inf_active else deenergized_color
        tr_color_bot = mv_color if bus_inf_active else deenergized_color
        tr_arrow_color = energized_green if bus_inf_active else deenergized_color
        tr_text_status = "ENERGIZADO" if bus_inf_active else "DESENERGIZADO"
        r_tr = min(width, height) * 0.045
        r_tr = max(20, min(32, r_tr))

        self._draw_transformer_tr(c, width * 0.50, height * 0.76, height * 0.76 + r_tr * 1.45, r_tr, tr_color_top, tr_color_bot, tr_arrow_color)

        box_x1 = width * 0.50 + r_tr + 16
        box_y1 = height * 0.71
        box_x2 = box_x1 + 215
        box_y2 = box_y1 + 98
        c.create_rectangle(box_x1, box_y1, box_x2, box_y2, fill="#0d1117", outline="#37474f", width=1.5)
        c.create_text(box_x1 + 10, box_y1 + 14, anchor="w", text=f"TR1: {tr_voltage:.1f} kV | TAP {tr_tap}", fill=energized_green if bus_inf_active else "#e53935", font=("Consolas", 8, "bold"))
        c.create_text(box_x1 + 10, box_y1 + 32, anchor="w", text=f"CORR: {current_val:.1f} A | FREQ: {freq_val:.2f} Hz", fill="#ffd600" if bus_inf_active else "#90a4ae", font=("Consolas", 8))
        c.create_text(box_x1 + 10, box_y1 + 50, anchor="w", text=f"TEMP: {tr_temp}°C | {tr_text_status}", fill=energized_green if bus_inf_active else "#e53935", font=("Consolas", 8, "bold"))
        c.create_text(box_x1 + 10, box_y1 + 68, anchor="w", text=f"81U/81O: {'TRIP' if any_81_trip else 'OK'}", fill="#ff5252" if any_81_trip else "#00ff00", font=("Consolas", 8))
        c.create_text(box_x1 + 10, box_y1 + 86, anchor="w", text=f"LATÊNCIA: {tr_latency} ms", fill="#64b5f6", font=("Consolas", 8))

        c.create_text(width * 0.50, height * 0.95, text=f"TR1 (230/13.8kV) [{tr_text_status}] | Freq: {freq_val:.2f}Hz | Corr: {current_val:.1f}A | Latência: {tr_latency}ms", fill=energized_green if bus_inf_active else "#e53935", font=("Consolas", 9, "bold"))

    # =========================================================================
    # TELECOMANDO & INTERTRAVAMENTOS (CILO)
    # =========================================================================
    def build_control_tab(self):
        tab = self.tab_control
        ctk.CTkLabel(tab, text="Módulo de Telecomando e Intertravamentos Lógicos CILO (230kV)", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=12)

        self.lbl_cilo_feedback = ctk.CTkLabel(
            tab,
            text="CILO ATIVO: Intertravamentos acoplados aos estados reais de 52L1, 52L2, 89BP e 89T1 no unifilar.",
            font=ctk.CTkFont(size=11, family="Consolas"),
            text_color="#64b5f6"
        )
        self.lbl_cilo_feedback.pack(pady=(0, 8))

        frame_cmds = ctk.CTkScrollableFrame(tab, fg_color="#1f1f1f", height=420)
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

        with self.state_lock:
            states = {bay_id: self.bay_status[bay_id]["closed"] for bay_id in self.control_buttons}

        for bay_id, btns in self.control_buttons.items():
            is_closed = states[bay_id]
            if is_closed:
                btns["abrir"].configure(fg_color=open_red, hover_color=open_red_hover, text_color="white", border_width=2, border_color="#ef9a9a")
                btns["fechar"].configure(fg_color=disabled_gray, hover_color=disabled_gray, text_color="#9e9e9e", border_width=0)
            else:
                btns["fechar"].configure(fg_color=close_green, hover_color=close_green_hover, text_color="white", border_width=2, border_color="#a5d6a7")
                btns["abrir"].configure(fg_color=disabled_gray, hover_color=disabled_gray, text_color="#9e9e9e", border_width=0)

    # =========================================================================
    # PROTEÇÃO ANSI (INCLUINDO 81U SUBFREQUÊNCIA E 81O SOBREFREQUÊNCIA)
    # =========================================================================
    def build_protection_tab(self):
        tab = self.tab_prot
        tab.grid_rowconfigure(3, weight=1)
        tab.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(tab, text="Módulo de Proteção ANSI Completa - Funções Dedicadas + 81U / 81O", font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, pady=8)

        p_frame = ctk.CTkFrame(tab, fg_color="#1f1f1f")
        p_frame.grid(row=1, column=0, sticky="ew", padx=20, pady=4)

        self.lbl_prot_status = ctk.CTkLabel(
            p_frame,
            text="Relés ANSI (50/51, 21, 87T, 67, 25, 59, 81U, 81O): OPERANDO DENTRO DOS LIMITES",
            font=ctk.CTkFont(size=11, family="Consolas"),
            text_color="#00ff00"
        )
        self.lbl_prot_status.pack(padx=20, pady=10)

        btn_grid = ctk.CTkFrame(p_frame, fg_color="transparent")
        btn_grid.pack(padx=20, pady=(0, 10))

        ctk.CTkButton(btn_grid, text="Trip 50/51", fg_color="#c0392b", width=95, command=lambda: self.simulate_ansi_trip("50/51", "Abertura 52L1 por Sobrecorrente")).pack(side="left", padx=3)
        ctk.CTkButton(btn_grid, text="Trip 87T", fg_color="#c0392b", width=95, command=lambda: self.simulate_ansi_trip("87T", "Trip Diferencial TR1")).pack(side="left", padx=3)
        ctk.CTkButton(btn_grid, text="Trip 21", fg_color="#d35400", width=95, command=lambda: self.simulate_ansi_trip("21", "Proteção de Distância Zona 1")).pack(side="left", padx=3)
        ctk.CTkButton(btn_grid, text="Trip 67", fg_color="#e67e22", width=95, command=lambda: self.simulate_ansi_trip("67", "Sobrecorrente Direcional")).pack(side="left", padx=3)
        ctk.CTkButton(btn_grid, text="Trip 25", fg_color="#8e44ad", width=95, command=lambda: self.simulate_ansi_trip("25", "Checagem de Sincronismo")).pack(side="left", padx=3)
        ctk.CTkButton(btn_grid, text="Trip 59", fg_color="#2980b9", width=95, command=lambda: self.simulate_ansi_trip("59", "Proteção de Sobretensão 230kV")).pack(side="left", padx=3)

        f81_frame = ctk.CTkFrame(tab, fg_color="#1b222c")
        f81_frame.grid(row=2, column=0, sticky="ew", padx=20, pady=6)

        ctk.CTkLabel(f81_frame, text="Ajustes & Simulação Frequência ANSI 81U (Subfrequência) / 81O (Sobrefrequência):", font=ctk.CTkFont(size=12, weight="bold"), text_color="#ffd600").pack(anchor="w", padx=12, pady=(8, 4))

        row_cfg = ctk.CTkFrame(f81_frame, fg_color="transparent")
        row_cfg.pack(fill="x", padx=12, pady=4)

        ctk.CTkLabel(row_cfg, text="81U Pickup (Hz):", font=ctk.CTkFont(size=11, family="Consolas")).pack(side="left", padx=(0, 4))
        self.entry_81u_hz = ctk.CTkEntry(row_cfg, width=65)
        self.entry_81u_hz.insert(0, "59.50")
        self.entry_81u_hz.pack(side="left", padx=(0, 10))

        ctk.CTkLabel(row_cfg, text="81U Delay (s):", font=ctk.CTkFont(size=11, family="Consolas")).pack(side="left", padx=(0, 4))
        self.entry_81u_t = ctk.CTkEntry(row_cfg, width=55)
        self.entry_81u_t.insert(0, "0.50")
        self.entry_81u_t.pack(side="left", padx=(0, 14))

        ctk.CTkLabel(row_cfg, text="81O Pickup (Hz):", font=ctk.CTkFont(size=11, family="Consolas")).pack(side="left", padx=(0, 4))
        self.entry_81o_hz = ctk.CTkEntry(row_cfg, width=65)
        self.entry_81o_hz.insert(0, "60.50")
        self.entry_81o_hz.pack(side="left", padx=(0, 10))

        ctk.CTkLabel(row_cfg, text="81O Delay (s):", font=ctk.CTkFont(size=11, family="Consolas")).pack(side="left", padx=(0, 4))
        self.entry_81o_t = ctk.CTkEntry(row_cfg, width=55)
        self.entry_81o_t.insert(0, "0.50")
        self.entry_81o_t.pack(side="left", padx=(0, 10))

        ctk.CTkButton(row_cfg, text="Aplicar Ajustes 81", width=120, fg_color="#16a085", command=self.apply_81_settings).pack(side="left", padx=4)

        row_sim = ctk.CTkFrame(f81_frame, fg_color="transparent")
        row_sim.pack(fill="x", padx=12, pady=(4, 10))

        ctk.CTkLabel(row_sim, text="Freq. Simulada (Hz):", font=ctk.CTkFont(size=11, family="Consolas")).pack(side="left", padx=(0, 6))
        self.slider_freq = ctk.CTkSlider(row_sim, from_=57.5, to=62.5, number_of_steps=100, width=180, command=self._on_freq_slider)
        self.slider_freq.set(60.0)
        self.slider_freq.pack(side="left", padx=4)

        self.lbl_freq_val = ctk.CTkLabel(row_sim, text="60.00 Hz (Nominal)", font=ctk.CTkFont(size=11, family="Consolas"), text_color="#00ff00")
        self.lbl_freq_val.pack(side="left", padx=8)

        ctk.CTkButton(row_sim, text="Simular Afundamento (81U)", width=155, fg_color="#c0392b", command=self.trigger_81u_test).pack(side="left", padx=4)
        ctk.CTkButton(row_sim, text="Simular Elevação (81O)", width=145, fg_color="#d35400", command=self.trigger_81o_test).pack(side="left", padx=4)
        ctk.CTkButton(row_sim, text="Retomar 60 Hz", width=110, fg_color="#2980b9", command=self.reset_freq_nominal).pack(side="left", padx=4)

        log_frame = ctk.CTkFrame(tab, fg_color="#141414")
        log_frame.grid(row=3, column=0, sticky="nsew", padx=20, pady=8)
        log_frame.grid_rowconfigure(1, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(log_frame, text="Área de Registros e Logs de Disparos de Todas as Funções ANSI (Trip Log / SOE):", font=ctk.CTkFont(size=12, weight="bold")).grid(row=0, column=0, sticky="w", padx=10, pady=5)

        self.prot_log_box = ctk.CTkTextbox(log_frame, font=ctk.CTkFont(family="Consolas", size=10), fg_color="#1a1a1a")
        self.prot_log_box.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self.prot_log_box.insert("0.0", f"[{datetime.now().strftime('%H:%M:%S')}] [PROT] Matriz de relés ANSI + 81U/81O armada na subestação 230kV.\n")
        self.prot_log_box.configure(state="disabled")

    def apply_81_settings(self):
        try:
            u_hz = float(self.entry_81u_hz.get().replace(",", "."))
            u_t = max(0.1, float(self.entry_81u_t.get().replace(",", ".")))
            o_hz = float(self.entry_81o_hz.get().replace(",", "."))
            o_t = max(0.1, float(self.entry_81o_t.get().replace(",", ".")))

            if u_hz >= o_hz:
                self.log_alarm("[PROT 81] Ajuste inválido: Pickup 81U deve ser menor que Pickup 81O.\n")
                return

            with self.state_lock:
                self.prot_81["81U_pickup"] = u_hz
                self.prot_81["81U_delay"] = u_t
                self.prot_81["81O_pickup"] = o_hz
                self.prot_81["81O_delay"] = o_t
                self.prot_81["81U_tripped"] = False
                self.prot_81["81O_tripped"] = False

            if hasattr(self, "hline_81u") and hasattr(self, "hline_81o"):
                self.hline_81u.set_ydata([u_hz, u_hz])
                self.hline_81o.set_ydata([o_hz, o_hz])
                self.canvas.draw()

            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            msg = f"[{ts}] [PROT 81] Ajustes aplicados: 81U={u_hz:.2f}Hz ({u_t:.2f}s) | 81O={o_hz:.2f}Hz ({o_t:.2f}s).\n"
            self.log_prot(msg)
            self.log_soe(msg)
            self.send_udp_state_to_c()
        except Exception:
            self.log_alarm("[PROT 81] Erro ao ler valores numéricos de 81U / 81O.\n")

    def _on_freq_slider(self, val):
        with self.state_lock:
            self.freq_override_active = True
            self.freq_target_hz = round(float(val), 2)
            target = self.freq_target_hz
        self.lbl_freq_val.configure(text=f"{target:.2f} Hz (Manual)")

    def trigger_81u_test(self):
        with self.state_lock:
            target = min(self.prot_81["81U_pickup"] - 0.35, 59.15)
            target = max(57.50, round(target, 2))
            self.freq_override_active = True
            self.freq_target_hz = target
        self.slider_freq.set(target)
        self.lbl_freq_val.configure(text=f"{target:.2f} Hz (Teste 81U)", text_color="#e74c3c")

    def trigger_81o_test(self):
        with self.state_lock:
            target = max(self.prot_81["81O_pickup"] + 0.35, 60.85)
            target = min(62.50, round(target, 2))
            self.freq_override_active = True
            self.freq_target_hz = target
        self.slider_freq.set(target)
        self.lbl_freq_val.configure(text=f"{target:.2f} Hz (Teste 81O)", text_color="#f39c12")

    def reset_freq_nominal(self):
        with self.state_lock:
            self.freq_override_active = False
            self.freq_target_hz = 60.0
            self.prot_81["81U_tripped"] = False
            self.prot_81["81O_tripped"] = False
            self.prot_81["81U_timer"] = 0.0
            self.prot_81["81O_timer"] = 0.0
        self.slider_freq.set(60.0)
        self.lbl_freq_val.configure(text="60.00 Hz (Nominal)", text_color="#00ff00")
        self.send_udp_state_to_c()

    def _evaluate_ansi_81(self, dt_sec):
        pending_trips = []
        with self.state_lock:
            if not self.has_any_voltage_reference():
                self.prot_81["81U_timer"] = 0.0
                self.prot_81["81O_timer"] = 0.0
                self.prot_81["81U_pickup_active"] = False
                self.prot_81["81O_pickup_active"] = False
                return

            f_now = self.freq_hz

            if self.prot_81["81U_enabled"] and f_now <= self.prot_81["81U_pickup"]:
                self.prot_81["81U_pickup_active"] = True
                self.prot_81["81U_timer"] += dt_sec
                if self.prot_81["81U_timer"] >= self.prot_81["81U_delay"] and not self.prot_81["81U_tripped"]:
                    self.prot_81["81U_tripped"] = True
                    pending_trips.append(("81U", f"Subfrequência ({f_now:.2f} Hz <= {self.prot_81['81U_pickup']:.2f} Hz)"))
            else:
                self.prot_81["81U_pickup_active"] = False
                self.prot_81["81U_timer"] = 0.0

            if self.prot_81["81O_enabled"] and f_now >= self.prot_81["81O_pickup"]:
                self.prot_81["81O_pickup_active"] = True
                self.prot_81["81O_timer"] += dt_sec
                if self.prot_81["81O_timer"] >= self.prot_81["81O_delay"] and not self.prot_81["81O_tripped"]:
                    self.prot_81["81O_tripped"] = True
                    pending_trips.append(("81O", f"Sobrefrequência ({f_now:.2f} Hz >= {self.prot_81['81O_pickup']:.2f} Hz)"))
            else:
                self.prot_81["81O_pickup_active"] = False
                self.prot_81["81O_timer"] = 0.0

        for ansi_code, desc in pending_trips:
            self.simulate_ansi_trip(ansi_code, desc)

    # =========================================================================
    # ALARMES, TENDÊNCIAS (COM FREQUÊNCIA HZ), NÓS IEC 61850 E SOE
    # =========================================================================
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

        ctk.CTkLabel(header_tr, text="Gráficos de Tendência Estilo SAGE (Corrente, Tensão & Frequência Hz)", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left")

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
        self.ax3 = self.ax1.twinx()
        self.ax3.spines["right"].set_position(("axes", 1.09))

        self.line1, = self.ax1.plot(self.x_data, self.y_curr, color='#3498db', label='Corrente L1 (A)')
        self.line2, = self.ax2.plot(self.x_data, self.y_volt, color='#e67e22', label='Tensão Nominal (kV)')
        self.line3, = self.ax3.plot(self.x_data, self.y_freq, color='#00e5ff', linewidth=1.6, label='Frequência (Hz)')

        self.hline_81u = self.ax3.axhline(y=self.prot_81["81U_pickup"], color='#ff5252', linestyle=':', linewidth=1.1, label='Ajuste 81U (Hz)')
        self.hline_81o = self.ax3.axhline(y=self.prot_81["81O_pickup"], color='#ffd600', linestyle=':', linewidth=1.1, label='Ajuste 81O (Hz)')

        self.ax1.set_ylabel('Corrente [A]', color='#3498db', fontsize=9, fontweight='bold')
        self.ax2.set_ylabel('Tensão [kV]', color='#e67e22', fontsize=9, fontweight='bold')
        self.ax3.set_ylabel('Frequência [Hz]', color='#00e5ff', fontsize=9, fontweight='bold')
        self.ax1.set_xlabel('Buffer Amostral Histórico (Tempo)', color='white', fontsize=9)

        self.ax1.set_xlim(self.view_start, self.view_start + self.window_size)
        self.ax3.set_ylim(57.0, 63.0)

        self.v1 = self.ax1.axvline(x=self.cursor_x1, color='#e74c3c', linestyle='--', linewidth=2, label='Pena 1 (Início Falta)')
        self.v2 = self.ax1.axvline(x=self.cursor_x2, color='#00ff00', linestyle='--', linewidth=2, label='Pena 2 (Fim Falta)')

        self.ax1.tick_params(colors='white', labelsize=8)
        self.ax2.tick_params(colors='white', labelsize=8)
        self.ax3.tick_params(colors='#00e5ff', labelsize=8)
        self.ax1.xaxis.label.set_color('white')
        for spine in self.ax1.spines.values():
            spine.set_color('#555555')
        for spine in self.ax2.spines.values():
            spine.set_color('#555555')
        for spine in self.ax3.spines.values():
            spine.set_color('#555555')

        lines_1, labels_1 = self.ax1.get_legend_handles_labels()
        lines_2, labels_2 = self.ax2.get_legend_handles_labels()
        lines_3, labels_3 = self.ax3.get_legend_handles_labels()
        self.ax1.legend(lines_1 + lines_2 + lines_3, labels_1 + labels_2 + labels_3, loc='upper left', fontsize=7, facecolor='#1f1f1f', labelcolor='white')

        self.fig.subplots_adjust(right=0.82)

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
        freq_p1 = self.y_freq[self.cursor_x1]
        curr_p2 = self.y_curr[self.cursor_x2]
        volt_p2 = self.y_volt[self.cursor_x2]
        freq_p2 = self.y_freq[self.cursor_x2]

        self.log_alarm(
            f"[ANÁLISE SAGE] Δt={dt_diff}s | Pena 1 (idx {self.cursor_x1}): {curr_p1:.1f}A, {volt_p1:.2f}kV, {freq_p1:.2f}Hz | "
            f"Pena 2 (idx {self.cursor_x2}): {curr_p2:.1f}A, {volt_p2:.2f}kV, {freq_p2:.2f}Hz\n"
        )

    def build_nodes_tab(self):
        tab = self.tab_nodes
        tab.grid_rowconfigure(1, weight=1)
        tab.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(tab, text="Árvore de Nós Lógicos IEC 61850 (Subestação 230kV — GOOSE / SV / MMS)", font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, pady=10)

        self.tree_box = ctk.CTkTextbox(tab, font=ctk.CTkFont(family="Consolas", size=11), fg_color="#111111")
        self.tree_box.grid(row=1, column=0, sticky="nsew", padx=15, pady=10)
        self.refresh_nodes_tree()

    def refresh_nodes_tree(self):
        if not hasattr(self, "tree_box"):
            return
        with self.mms_lock:
            bind_str = f"{self.mms_bind_ip}:{self.mms_port_active}" if (self.mms_running and self.mms_port_active and self.mms_bind_ip) else "PARADO"
        with self.sv_lock:
            sv_info = f"ATIVO ({self.sv_id} | smpCnt={self.sv_smp_cnt})" if self.sv_running else "INATIVO"
        with self.goose_lock:
            st_num = self.goose_st_num
            sq_num = self.goose_sq_num
            ev_id = self.goose_event_id
        with self.state_lock:
            st_52l1 = 'CLOSED' if self.bay_status['52L1']['closed'] else 'OPEN'
            st_89bp = 'CLOSED' if self.bay_status['89BP']['closed'] else 'OPEN'
            st_89t1 = 'CLOSED' if self.bay_status['89T1']['closed'] else 'OPEN'
            hz_v = self.freq_hz
            kv_v = self.transf_status['voltage']
            amp_v = self.transf_status['current']
            u81_t = self.prot_81['81U_tripped']
            u81_p = self.prot_81['81U_pickup']
            o81_t = self.prot_81['81O_tripped']
            o81_p = self.prot_81['81O_pickup']

        mms_info = f"TCP {bind_str}"
        tree_text = (
            "📁 E230kV_Substation\n"
            f" ├─ 🌐 MMS Server (IEC 61850-8-1): {mms_info}\n"
            f" ├─ 📡 Sampled Values (IEC 61850-9-2LE): {sv_info}\n"
            " └─ 📁 Bay_L1_TR1 (230/13.8kV)\n"
            f"    ├─ 📄 XCBR1.Pos.stVal = {st_52l1} (52L1)\n"
            f"    ├─ 📄 XSWI1_BP.Pos.stVal = {st_89bp} (89BP)\n"
            f"    ├─ 📄 XSWI2_T1.Pos.stVal = {st_89t1} (89T1)\n"
            f"    ├─ 📄 CSWI1.Pos.ctlModel = direct-with-normal-security (UDP {UDP_C_SERVER_PORT}/{UDP_PY_CMD_PORT})\n"
            f"    ├─ 📄 MMXU1.Hz.mag.f = {hz_v:.2f} Hz | V = {kv_v:.1f} kV | I = {amp_v:.1f} A\n"
            f"    ├─ 📄 PTUF1 (ANSI 81U).Op.general = {u81_t} (Pickup: {u81_p:.2f} Hz)\n"
            f"    ├─ 📄 PTOF1 (ANSI 81O).Op.general = {o81_t} (Pickup: {o81_p:.2f} Hz)\n"
            f"    └─ 🌐 GOOSE Dataset (stNum={st_num}, sqNum={sq_num}, Evento={ev_id})\n"
        )
        self.tree_box.configure(state="normal")
        self.tree_box.delete("0.0", "end")
        self.tree_box.insert("0.0", tree_text)
        self.tree_box.configure(state="disabled")

    def build_soe_tab(self):
        tab = self.tab_soe
        tab.grid_rowconfigure(1, weight=1)
        tab.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(tab, text="Logger de Sequência de Eventos (SOE - Sequence of Events)", font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, pady=10)

        self.soe_box = ctk.CTkTextbox(tab, font=ctk.CTkFont(family="Consolas", size=10), fg_color="#111111")
        self.soe_box.grid(row=1, column=0, sticky="nsew", padx=15, pady=10)
        self.soe_box.insert("0.0", f"[{datetime.now().strftime('%H:%M:%S.000')}] [SOE] Inicialização completa dos relés ANSI, 81U/81O e lógica CILO.\n")
        self.soe_box.configure(state="disabled")

    # =========================================================================
    # PROTOCOLO IEC 61850 GOOSE (LAYER 2 / ETHERTYPE 0x88B8)
    # =========================================================================
    def _get_source_mac(self, iface_name):
        resolved = _resolve_scapy_iface(iface_name)
        if SCAPY_AVAILABLE and resolved and callable(get_if_hwaddr):
            try:
                mac = get_if_hwaddr(resolved)
                if mac and mac != "00:00:00:00:00:00":
                    return mac
            except Exception:
                pass
        return "02:42:ac:11:00:02"

    def _build_and_send_goose_frame(self, iface_name, event_id, st_num, sq_num, val_bool, tal_ms=2000):
        resolved_iface = _resolve_scapy_iface(iface_name)
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

        src_mac = self._get_source_mac(resolved_iface)
        ether_frame = Ether(src=src_mac, dst=GOOSE_MULTICAST_MAC, type=0x88B8) / Raw(load=payload_completo)
        sendp(ether_frame, iface=resolved_iface, verbose=False)

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
        iface_escolhida = (self.interface_selecionada.get() or self._selected_iface_name).strip()
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
        except Exception as exc:
            self._report_protocol_error("GOOSE", exc)

    def _loop_heartbeat_goose(self):
        while self.running:
            time.sleep(2.0)
            if not self.running or not SCAPY_AVAILABLE:
                continue
            iface_escolhida = (self._selected_iface_name or self.interface_selecionada.get()).strip()
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
            except Exception as exc:
                self._report_protocol_error("GOOSE", exc)

    # =========================================================================
    # PROTOCOLO IEC 61850 SAMPLED VALUES (9-2LE / ETHERTYPE 0x88BA)
    # =========================================================================
    def toggle_sv(self):
        if not SCAPY_AVAILABLE:
            self.log_alarm("[SV 9-2LE] Scapy/Npcap não disponível nesta sessão para envio Layer 2.\n")
            return
        with self.sv_lock:
            self.sv_running = not self.sv_running
            sv_on = self.sv_running
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        if sv_on:
            self.btn_sv_toggle.configure(text="SV: ON", fg_color="#1b8a4b", hover_color="#146c3a")
            msg = f"[{ts}] [SV 9-2LE] Emissão Sampled Values iniciada ({self.sv_id} | EtherType 0x88BA) na placa '{self.interface_selecionada.get()}'.\n"
        else:
            self.btn_sv_toggle.configure(text="SV: OFF", fg_color="#37474f", hover_color="#455a64")
            msg = f"[{ts}] [SV 9-2LE] Emissão Sampled Values interrompida.\n"
        self.log_alarm(msg)
        self.log_soe(msg)

    def _build_sv_frame(self, src_mac, smp_cnt, phase_rad, v_peak_v, i_peak_a):
        ia = int(i_peak_a * math.sin(phase_rad))
        ib = int(i_peak_a * math.sin(phase_rad - 2.0 * math.pi / 3.0))
        ic = int(i_peak_a * math.sin(phase_rad + 2.0 * math.pi / 3.0))
        in_val = ia + ib + ic

        va = int(v_peak_v * math.sin(phase_rad))
        vb = int(v_peak_v * math.sin(phase_rad - 2.0 * math.pi / 3.0))
        vc = int(v_peak_v * math.sin(phase_rad + 2.0 * math.pi / 3.0))
        vn = va + vb + vc

        quality = b'\x00\x00\x00\x00'
        seq_data = b""
        for ch in (ia, ib, ic, in_val, va, vb, vc, vn):
            seq_data += int(ch).to_bytes(4, 'big', signed=True) + quality

        sv_id_tlv = self._ber_tlv(b'\x80', self.sv_id.encode('ascii'))
        smp_cnt_tlv = self._ber_tlv(b'\x82', int(smp_cnt & 0xFFFF).to_bytes(2, 'big'))
        conf_rev_tlv = self._ber_tlv(b'\x83', b'\x01')
        smp_synch_tlv = self._ber_tlv(b'\x85', b'\x01')
        seq_data_tlv = self._ber_tlv(b'\x87', seq_data)

        asdu = self._ber_tlv(b'\x30', sv_id_tlv + smp_cnt_tlv + conf_rev_tlv + smp_synch_tlv + seq_data_tlv)
        no_asdu_tlv = self._ber_tlv(b'\x80', b'\x01')
        seq_asdu_tlv = self._ber_tlv(b'\xA2', asdu)
        sav_pdu = self._ber_tlv(b'\x60', no_asdu_tlv + seq_asdu_tlv)

        pdu_len = len(sav_pdu) + 8
        sv_header = b'\x40\x00' + pdu_len.to_bytes(2, 'big') + b'\x00\x00\x00\x00'
        return Ether(src=src_mac, dst=SV_MULTICAST_MAC, type=0x88BA) / Raw(load=sv_header + sav_pdu)

    def _loop_sampled_values(self):
        batch_samples = 48
        batch_interval = batch_samples / 4800.0
        while self.running:
            t_start = time.perf_counter()
            with self.sv_lock:
                sv_on = self.sv_running
            if sv_on and SCAPY_AVAILABLE:
                iface = _resolve_scapy_iface(self._selected_iface_name)
                if iface:
                    try:
                        src_mac = self._get_source_mac(iface)
                        with self.state_lock:
                            freq = max(0.0, float(self.freq_hz))
                            v_rms = float(self.transf_status["voltage"]) * 1000.0 / math.sqrt(3.0)
                            i_rms = float(self.transf_status["current"])
                        v_peak = v_rms * math.sqrt(2.0)
                        i_peak = i_rms * math.sqrt(2.0)

                        frames = []
                        with self.sv_lock:
                            for _ in range(batch_samples):
                                omega = 2.0 * math.pi * freq
                                dt = 1.0 / 4800.0
                                self.phase_angle = (self.phase_angle + omega * dt) % (2.0 * math.pi)
                                frames.append(self._build_sv_frame(src_mac, self.sv_smp_cnt, self.phase_angle, v_peak, i_peak))
                                self.sv_smp_cnt = (self.sv_smp_cnt + 1) % 4800

                        if SENDPFAST_AVAILABLE and _scapy_sendpfast is not None:
                            _scapy_sendpfast(frames, iface=iface, parse_results=False)
                        else:
                            sendp(frames, iface=iface, verbose=False)
                    except Exception as exc:
                        self._report_protocol_error("SV", exc)

            elapsed = time.perf_counter() - t_start
            sleep_time = max(0.002, batch_interval - elapsed)
            time.sleep(sleep_time)

    # =========================================================================
    # PROTOCOLO IEC 61850 MMS (C EXE + PONTE UDP + FALLBACK PYTHON)
    # =========================================================================
    def toggle_mms(self):
        with self.mms_lock:
            is_on = self.mms_running
        if is_on:
            self.stop_mms_server()
            self.btn_mms_toggle.configure(text="MMS: OFF", fg_color="#37474f", hover_color="#455a64")
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            self.log_alarm(f"[{ts}] [MMS] Servidor IEC 61850 MMS encerrado.\n")
        else:
            threading.Thread(target=self._toggle_mms_on_worker, daemon=True).start()

    def _toggle_mms_on_worker(self):
        self.start_mms_server()
        with self.mms_lock:
            is_on = self.mms_running
        if is_on:
            self._ui_call(self.btn_mms_toggle.configure, text="MMS: ON", fg_color="#1b8a4b", hover_color="#146c3a")

    def _stream_c_mms_output(self, proc):
        try:
            if proc.stdout is None:
                return
            for line in proc.stdout:
                if not self.running:
                    break
                text = line.strip()
                if not text:
                    continue
                if "porta=" in text:
                    try:
                        port_part = text.split("porta=", 1)[1].split(")", 1)[0].strip().rstrip(".,; ")
                        active_port = int(port_part)
                        if 1 <= active_port <= 65535:
                            with self.mms_lock:
                                self.mms_port_active = active_port
                                self.mms_running = True
                                if "bind=" in text:
                                    self.mms_bind_ip = text.split("bind=", 1)[1].split(",", 1)[0].strip()
                    except (ValueError, IndexError):
                        pass
                    self._ui_call(self.update_mms_badge)
                    self._ui_call(self.refresh_nodes_tree)
                elif "Falha ao iniciar IedServer" in text:
                    with self.mms_lock:
                        self.mms_last_error = text
                    self._ui_call(self.update_mms_badge)
        except Exception:
            pass

    def _start_c_mms_process(self, bind_ip: str) -> bool:
        exe_path = resolve_c_mms_exe_path()
        if not os.path.isfile(exe_path):
            return False
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            proc = subprocess.Popen(
                [exe_path, bind_ip],
                cwd=os.path.dirname(exe_path) or None,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=creationflags,
            )
            time.sleep(0.25)
            if proc.poll() is not None:
                return False

            with self.mms_lock:
                self._c_mms_proc = proc
                self.mms_bind_ip = bind_ip
                self.mms_port_active = 102
                self.mms_last_error = ""
                self.mms_running = True

            self._c_mms_log_thread = threading.Thread(
                target=self._stream_c_mms_output, args=(proc,), daemon=True
            )
            self._c_mms_log_thread.start()
            self.send_udp_state_to_c()
            return True
        except Exception:
            return False

    def start_mms_server(self, preferred_ip=None):
        with self.mms_lock:
            if self.mms_running:
                self._ui_call(self.update_mms_badge)
                return

            iface = self._selected_iface_name
            target_ip = preferred_ip or self.resolve_interface_ipv4(iface)

        if self._start_c_mms_process(target_ip) or (target_ip != "0.0.0.0" and self._start_c_mms_process("0.0.0.0")):
            self._ui_call(self.update_mms_badge)
            self._ui_call(self.refresh_nodes_tree)
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            self._ui_call(self.log_soe, f"[{ts}] [MMS] Processo C ({C_MMS_EXE_NAME}) iniciado para {target_ip} (UDP {UDP_C_SERVER_PORT}/{UDP_PY_CMD_PORT}).\n")
            return

        with self.mms_lock:
            candidatos_ip = [target_ip] if target_ip else []
            if "0.0.0.0" not in candidatos_ip:
                candidatos_ip.append("0.0.0.0")

            last_err = ""
            for bind_ip in candidatos_ip:
                for port in (102, 10102):
                    srv = None
                    try:
                        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        srv.settimeout(0.5)
                        srv.bind((bind_ip, port))
                        srv.listen(5)
                        self._mms_sock = srv
                        self.mms_bind_ip = bind_ip
                        self.mms_port_active = port
                        self.mms_last_error = ""
                        self.mms_running = True
                        threading.Thread(target=self._loop_mms_server, daemon=True).start()
                        self._ui_call(self.update_mms_badge)
                        self._ui_call(self.refresh_nodes_tree)
                        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                        self._ui_call(self.log_soe, f"[{ts}] [MMS] Servidor IEC 61850 MMS (Python) escutando em {bind_ip}:{port} (Placa: {iface}).\n")
                        return
                    except Exception as exc:
                        last_err = str(exc)
                        if srv is not None:
                            try:
                                srv.close()
                            except Exception:
                                pass

            self.mms_running = False
            self.mms_port_active = None
            self.mms_bind_ip = None
            self.mms_last_error = last_err or "Falha no bind"

        self._ui_call(self.update_mms_badge)
        self._ui_call(self.refresh_nodes_tree)
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self._ui_call(self.log_alarm, f"[{ts}] [MMS] Falha ao iniciar servidor em {target_ip}: {self.mms_last_error}\n")

    def stop_mms_server(self):
        with self.mms_lock:
            self.mms_running = False
            self.mms_port_active = None
            self.mms_bind_ip = None
            proc = self._c_mms_proc
            self._c_mms_proc = None
            if self._mms_sock is not None:
                try:
                    self._mms_sock.close()
                except Exception:
                    pass
                self._mms_sock = None

        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

        self._ui_call(self.update_mms_badge)
        self._ui_call(self.refresh_nodes_tree)

    def _encode_ber_len(self, length: int) -> bytes:
        if length < 0x80:
            return bytes([length])
        if length < 0x100:
            return bytes([0x81, length])
        return bytes([0x82, (length >> 8) & 0xFF, length & 0xFF])

    def _ber_tlv(self, tag, value: bytes) -> bytes:
        if isinstance(tag, int):
            if not 0 <= tag <= 0xFF:
                raise ValueError(f"Tag BER fora do intervalo de um octeto: {tag}")
            tag_bytes = bytes((tag,))
        elif isinstance(tag, (bytes, bytearray)) and len(tag) == 1:
            tag_bytes = bytes(tag)
        else:
            raise TypeError("A tag BER deve ser um inteiro de 0 a 255 ou bytes de um octeto")
        if not isinstance(value, (bytes, bytearray)):
            raise TypeError("O valor BER deve ser bytes")
        value = bytes(value)
        return tag_bytes + self._encode_ber_len(len(value)) + value

    def _wrap_tpkt_cotp_dt(self, payload: bytes) -> bytes:
        cotp_dt = b"\x02\xf0\x80" + payload
        tpkt_len = 4 + len(cotp_dt)
        return b"\x03\x00" + tpkt_len.to_bytes(2, "big") + cotp_dt

    def _build_mms_initiate_response(self) -> bytes:
        init_resp_body = (
            self._ber_tlv(0x80, (1024).to_bytes(4, "big")) +
            self._ber_tlv(0x81, b"\x05") +
            self._ber_tlv(0x82, b"\x05") +
            self._ber_tlv(0x83, b"\x01") +
            self._ber_tlv(0xA4, self._ber_tlv(0x82, b"\xEE\x1C"))
        )
        invoke_id = self._ber_tlv(0x80, b"\x01")
        init_resp = self._ber_tlv(0x8B, init_resp_body)
        return self._ber_tlv(0xA1, invoke_id + init_resp)

    def _build_mms_namelist_response(self) -> bytes:
        identifiers = [
            b"E230kV_Substation",
            b"Bay_L1_TR1$XCBR1$Pos$stVal",
            b"Bay_L1_TR1$XSWI1_BP$Pos$stVal",
            b"Bay_L1_TR1$XSWI2_T1$Pos$stVal",
            b"Bay_L1_TR1$MMXU1$Hz$mag$f",
            b"Bay_L1_TR1$MMXU1$V$mag$f",
            b"Bay_L1_TR1$MMXU1$A$mag$f",
            b"Bay_L1_TR1$PTUF1$Op$general",
            b"Bay_L1_TR1$PTOF1$Op$general",
        ]
        seq_items = b"".join(self._ber_tlv(0x1A, item) for item in identifiers)
        list_of_id = self._ber_tlv(0xA0, seq_items)
        namelist_body = self._ber_tlv(0xA0, list_of_id)
        invoke_id = self._ber_tlv(0x80, b"\x02")
        namelist_resp = self._ber_tlv(0x81, namelist_body)
        return self._ber_tlv(0xA1, invoke_id + namelist_resp)

    def _build_mms_read_response(self) -> bytes:
        with self.state_lock:
            hz_bytes = f"{self.freq_hz:.2f}".encode("ascii")
            v_bytes = f"{self.transf_status['voltage']:.1f}".encode("ascii")
            a_bytes = f"{self.transf_status['current']:.1f}".encode("ascii")
            xcbr_bytes = b"CLOSED" if self.bay_status["52L1"]["closed"] else b"OPEN"
            u81_bytes = b"TRIP" if self.prot_81["81U_tripped"] else b"OK"
            o81_bytes = b"TRIP" if self.prot_81["81O_tripped"] else b"OK"

        access_results = b"".join([
            self._ber_tlv(0x84, xcbr_bytes),
            self._ber_tlv(0x87, hz_bytes),
            self._ber_tlv(0x87, v_bytes),
            self._ber_tlv(0x87, a_bytes),
            self._ber_tlv(0x83, b"\xff" if u81_bytes == b"TRIP" else b"\x00"),
            self._ber_tlv(0x83, b"\xff" if o81_bytes == b"TRIP" else b"\x00"),
        ])
        read_body = self._ber_tlv(0xA0, access_results)
        invoke_id = self._ber_tlv(0x80, b"\x03")
        read_resp = self._ber_tlv(0x84, read_body)
        return self._ber_tlv(0xA1, invoke_id + read_resp)

    def _handle_mms_client(self, conn: socket.socket):
        conn.settimeout(2.0)
        try:
            data = conn.recv(4096)
            if not data:
                return

            if len(data) >= 11 and data[0:2] == b"\x03\x00" and data[5] == 0xE0:
                client_src_ref = data[8:10]
                server_src_ref = b"\x00\x02"
                cotp_cc = bytes([0x06, 0xD0]) + client_src_ref + server_src_ref + b"\x00"
                tpkt_cc = b"\x03\x00" + (4 + len(cotp_cc)).to_bytes(2, "big") + cotp_cc
                conn.sendall(tpkt_cc)

                try:
                    data = conn.recv(4096)
                except socket.timeout:
                    return
                if not data:
                    return

            if b"\x8b" in data or b"\xa8" in data:
                mms_pdu = self._build_mms_initiate_response()
            elif b"\x01" in data and len(data) < 64:
                mms_pdu = self._build_mms_namelist_response()
            else:
                mms_pdu = self._build_mms_read_response()

            conn.sendall(self._wrap_tpkt_cotp_dt(mms_pdu))
        except Exception as exc:
            self._report_protocol_error("MMS", exc)

    def _loop_mms_server(self):
        while self.running and self.mms_running:
            srv = self._mms_sock
            if srv is None:
                break
            try:
                conn, _addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            except Exception:
                continue

            with conn:
                self._handle_mms_client(conn)

    # =========================================================================
    # COMANDOS, DISCREPÂNCIA, PROTEÇÃO ANSI E TELEMETRIA
    # =========================================================================
    def execute_command(self, bay_id, should_close):
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        with self.state_lock:
            allowed, reason = self._check_cilo_interlock(bay_id, should_close)
            if not allowed:
                msg = f"[{timestamp}] INTERTRAVAMENTO CILO: {reason}\n"
                if hasattr(self, "lbl_cilo_feedback"):
                    self.lbl_cilo_feedback.configure(text=f"BLOQUEIO CILO ({bay_id}): {reason}", text_color="#ff5252")
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
            bay_name = bay["name"]
            bay_latency = bay["latency"]

            self._recalc_electrical_state()

        if hasattr(self, "lbl_cilo_feedback"):
            self.lbl_cilo_feedback.configure(
                text=f"CILO OK: {bay_name} comutado para {status_str} sem violação de intertravamento.",
                text_color="#00ff00"
            )

        self.draw_mimic_diagram()
        self.update_control_buttons_visual()
        self.refresh_nodes_tree()

        self.enviar_pacote_goose(evento=f"POS_{bay_id}", status=status_str)
        self.send_udp_state_to_c()

        msg = f"[{timestamp}] COMANDO: {bay_name} comutado para {status_str} | Latência GOOSE/MMS: {bay_latency}ms.\n"
        self.log_alarm(msg)
        self.log_soe(msg)

    def trigger_pole_discrepancy(self):
        with self.state_lock:
            bay = self.bay_status["52L1"]
            bay["poles"]["A"] = True
            bay["poles"]["B"] = False
            bay["poles"]["C"] = True
            bay["discrepancy"] = True
            bay["latency"] = 11.4

        self.draw_mimic_diagram()
        self.update_control_buttons_visual()
        self.refresh_nodes_tree()

        self.enviar_pacote_goose(evento="DISCREPANCIA_POLOS_52L1", status="ALERTA")
        self.send_udp_state_to_c()

        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        msg = f"[{timestamp}] ALARME CRÍTICO: Discrepância de Polos no Disjuntor 52L1 (Fase B aberta em desacordo) [230kV]!\n"
        self.status_box.configure(text="ESTADO: ALARME DE DISCREPÂNCIA DE POLOS", text_color="#e74c3c")
        self.log_alarm(msg)
        self.log_soe(msg)

    def simulate_ansi_trip(self, ansi_code, description):
        with self.state_lock:
            self.transf_status["current"] = 0.0
            self.transf_status["voltage"] = 0.0
            self.transf_status["latency"] = 1.9

            self.bay_status["52L1"]["closed"] = False
            for p in self.bay_status["52L1"]["poles"]:
                self.bay_status["52L1"]["poles"][p] = False
            self.bay_status["52L1"]["discrepancy"] = False
            self.bay_status["52L1"]["current"] = 0.0

        self.lbl_prot_status.configure(text=f"TRIP ATUADO: ANSI {ansi_code} ({description})!", text_color="#e74c3c")
        self.draw_mimic_diagram()
        self.update_control_buttons_visual()
        self.refresh_nodes_tree()

        self.enviar_pacote_goose(evento=f"TRIP_ANSI_{ansi_code}", status="ATIVO")
        self.send_udp_state_to_c()

        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        msg = f"[{timestamp}] PROTEÇÃO: Atuação da Função ANSI [{ansi_code} - {description}] | Disjuntor 52L1 Aberto por GOOSE (Corrente e Tensão Zeradas).\n"
        self.log_alarm(msg)
        self.log_soe(msg)
        self.log_prot(f"[{timestamp}] TRIP ANSI {ansi_code} ({description}) | GOOSE Trip Enviado (Latência: 1.9ms).\n")

    def reset_patio(self):
        with self.state_lock:
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
            self.freq_override_active = False
            self.freq_target_hz = 60.0
            self.freq_hz = 60.0
            self.prot_81["81U_tripped"] = False
            self.prot_81["81O_tripped"] = False
            self.prot_81["81U_timer"] = 0.0
            self.prot_81["81O_timer"] = 0.0

        if hasattr(self, "slider_freq"):
            self.slider_freq.set(60.0)
        if hasattr(self, "lbl_freq_val"):
            self.lbl_freq_val.configure(text="60.00 Hz (Nominal)", text_color="#00ff00")

        self.lbl_prot_status.configure(text="Relés ANSI (50/51, 21, 87T, 67, 25, 59, 81U, 81O): OPERANDO DENTRO DOS LIMITES", text_color="#00ff00")
        status_txt = "ESTADO: NORMAL (GOOSE + MMS | VM Bridge Npcap)" if SCAPY_AVAILABLE else "ESTADO: NORMAL (Simulação + MMS)"
        self.status_box.configure(text=status_txt, text_color="#00ff00")
        self.draw_mimic_diagram()
        self.update_control_buttons_visual()
        self.refresh_nodes_tree()

        self.enviar_pacote_goose(evento="RESTABELECIMENTO_PATIO", status="NORMAL")
        self.send_udp_state_to_c()

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
        if not self.running:
            return

        self.update_mms_badge()
        self._drain_protocol_errors()

        if str(self.tabview.get()) == "Diagrama Unifilar":
            self.draw_mimic_diagram()

        with self.state_lock:
            energized = self.is_bay_energized()
            has_v = self.has_any_voltage_reference()
            base_curr = self.transf_status["current"]
            base_volt = self.transf_status["voltage"]
            override_on = self.freq_override_active
            target_f = self.freq_target_hz

        if energized:
            curr_val = base_curr + random.uniform(-2, 2)
            volt_val = base_volt + random.uniform(-0.05, 0.05)
        else:
            curr_val = 0.0
            volt_val = 0.0

        if not has_v:
            next_freq = 0.0
        elif override_on:
            next_freq = round(target_f + random.uniform(-0.01, 0.01), 3)
        else:
            next_freq = round(60.0 + random.uniform(-0.02, 0.02), 3)

        with self.state_lock:
            self.freq_hz = next_freq

        self._evaluate_ansi_81(1.0)

        with self.state_lock:
            freq_final = self.freq_hz

        self.y_curr.pop(0)
        self.y_curr.append(curr_val)

        self.y_volt.pop(0)
        self.y_volt.append(volt_val)

        self.y_freq.pop(0)
        self.y_freq.append(freq_final)

        self.refresh_nodes_tree()

        if not self.is_paused:
            self.view_start = self.buffer_size - self.window_size
            self.line1.set_ydata(self.y_curr)
            self.line2.set_ydata(self.y_volt)
            self.line3.set_ydata(self.y_freq)

            self.ax1.relim()
            self.ax1.autoscale_view()
            self.ax2.relim()
            self.ax2.autoscale_view()
            self.ax1.set_xlim(self.view_start, self.view_start + self.window_size)

            self.canvas.draw()

        self.send_udp_state_to_c()
        self.after(1000, self.update_telemetry)


if __name__ == "__main__":
    app = SubstationSCADAApp()
    app.mainloop()