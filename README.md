# ⚡ Supervisório para Simulação SCADA 230kV (Diário de um Comissionador - Ep. 08)
[![Baixar Executável](https://img.shields.io/badge/Download-.EXE%20(Release%20v1.0.8)-blue?style=for-the-badge&logo=windows)](https://github.com/araujosantanapatrick-creator/scada-subestacao-230kv/releases/latest/download/app_subestacao.zip)
Sistema supervisório para simulação SCADA desenvolvido em Python para automação, monitoramento e controle de subestações de alta tensão (230kV), integrando lógicas de intertravamento e protocolos de comunicação do setor elétrico.

## 🚀 Tecnologias e Ferramentas
* **Linguagem:** Python
* **Interface Gráfica (GUI):** CustomTkinter (Modo Escuro / Padrão Industrial)
* **Protocolos e Redes:** IEC 61850 (GOOSE)
* **Ferramentas de Diagnóstico:** OMICRON IEDScout, Wireshark

## ⚙️ Principais Funcionalidades e Telas do Sistema

### 1. Diagrama Unifilar (Mimic Diagram)
![Diagrama Unifilar](assets/print1.png)

### 2. Módulo de Telecomando e Intertravamentos CILO
![Telecomando](assets/print2.png)

### 3. Módulo de Proteção ANSI & TR1
![Proteção ANSI](assets/print3.png)

### 4. Painel de Alertas e Faltas (Annunciator)
![Painel de Almares](assets/print4.png)

### 5. Gráficos de Tendência Multi-Variável
![Gráficos de Tendência](assets/print5.png)

### 6. Árvore de Nós Lógicos IEC 61850
![Nodos IEC 61850](assets/print6.png)

### 7. Logger de Sequência de Eventos (SOE)
![Logger SOE](assets/print7.png)

## 📂 Estrutura do Projeto
* `app_subestacao.py` — Código principal da aplicação gráfica e motor de regras lógicas.
* `assets/` — Pasta contendo as capturas de tela do sistema.
* `.gitignore` — Arquivos ignorados pelo controle de versão.

---
*Desenvolvido por Patrick Araujo Santana — Série: Diário de um Comissionador.*
