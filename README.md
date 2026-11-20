# ⚡ Supervisório para Simulação SCADA 230kV (Diário de um Comissionador - Ep. 08)
[![Baixar Executável](https://img.shields.io/badge/Download-app__subestacao.exe-blue?style=for-the-badge&logo=windows)](https://github.com/araujosantanapatrick-creator/scada-subestacao-230kv/releases/download/v1.10/app_subestacao.exe)
Sistema supervisório para simulação SCADA desenvolvido em Python para automação, monitoramento e controle de subestações de alta tensão (230kV), integrando lógicas de intertravamento e protocolos de comunicação do setor elétrico.

## 🚀 Tecnologias e Ferramentas
- **Linguagem:** Python
- **Interface Gráfica (GUI):** CustomTkinter (Modo Escuro / Padrão Industrial)
- **Protocolos e Redes:** IEC 61850 (GOOSE)
- **Ferramentas de Diagnóstico:** OMICRON IEDScout, Wireshark

## ⚙️ Principais Funcionalidades e Telas do Sistema

### 1. Diagrama Unifilar (Mimic Diagram)
![Diagrama Unifilar](assets/01-diagrama-unifilar.png)

### 2. Seleção de Placa de Rede e Interfaces Npcap
![Seleção de Placa de Rede](assets/02-selecao-placa-rede.png)

### 3. Módulo de Telecomando e Intertravamentos CILO
![Telecomando](assets/03-telecomando-intertravamentos.png)

### 4. Módulo de Proteção ANSI & TR1
![Proteção ANSI](assets/04-protecao-ansi.png)

### 5. Painel de Alertas e Faltas (Annunciator)
![Painel de Alarmes](assets/05-painel-alarmes.png)

### 6. Gráficos de Tendência Multi-Variável
![Gráficos de Tendência](assets/06-graficos-tendencia.png)

### 7. Árvore de Nós Lógicos IEC 61850
![Nodos IEC 61850](assets/07-nos-logicos-iec61850.png)

### 8. Logger de Sequência de Eventos (SOE)
![Logger SOE](assets/08-logger-soe.png)

## 📂 Estrutura do Projeto
- `app_subestacao.py` — Código principal da aplicação gráfica e motor de regras lógicas.
- `assets/` — Pasta contendo as capturas de tela do sistema.
- `.gitignore` — Arquivos ignorados pelo controle de versão.

---
*Desenvolvido por Patrick Araujo Santana — Série: Diário de um Comissionador.*