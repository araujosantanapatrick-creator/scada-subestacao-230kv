\# ⚡ Supervisório SCADA 230kV (Diário de um Comissionador - Ep. 08)



Sistema supervisório SCADA desenvolvido em Python para automação, monitoramento e controle de subestações de alta tensão (230kV), integrando lógicas de intertravamento e protocolos de comunicação do setor elétrico.



\## 🚀 Tecnologias e Ferramentas

\* \*\*Linguagem:\*\* Python

\* \*\*Interface Gráfica (GUI):\*\* CustomTkinter (Modo Escuro / Padrão Industrial)

\* \*\*Protocolos e Redes:\*\* IEC 61850 (GOOSE)

\* \*\*Ferramentas de Diagnóstico:\*\* OMICRON IEDScout, Wireshark



\## ⚙️ Principais Funcionalidades

\* \*\*Diagrama Unifilar Dinâmico:\*\* Visualização de linhas, barramentos e fluxo de energia em tempo real.

\* \*\*Lógica CILO (Intertravamento):\*\* Bloqueios lógicos para impedir manobras indevidas em disjuntores e chaves seccionadoras.

\* \*\*Proteção ANSI 87T:\*\* Supervisão e sinalização de atuação da proteção diferencial de transformadores.

\* \*\*Telemetria de Disjuntores:\*\* Monitoramento de estado (`XCBR1.Pos.stVal`) e comandos remotos.



\## 📂 Estrutura do Projeto

\* `app\_subestacao.py` — Código principal da aplicação gráfica e motor de regras lógicas.

\* `.gitignore` — Arquivos ignorados pelo controle de versão.



\---

\*Desenvolvido por Patrick Araujo Santana — Série: Diário de um Comissionador.\*

