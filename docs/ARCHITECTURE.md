# Arquitetura

O repositório contém uma suíte de aplicativos com componentes compartilhados:

```text
ayo_desk/
├── app.py              central Ayo Desk e janelas independentes
├── network.py          libnm e perfis NetworkManager
├── bluetooth.py        BlueZ via D-Bus
├── tasks.py            operações assíncronas e pactl argumentado
├── player.py           GStreamer playbin
├── music/              Ayo Música: biblioteca, reprodução e integrações (sem GTK)
│   └── library.py      descoberta segura de arquivos
├── migrations.py       esquema SQLite versionado (PRAGMA user_version)
├── core.py             SQLite, configurações e ligação ctypes com a biblioteca C
└── *_page.py           telas GTK4/libadwaita
native/calc.c           parser matemático limitado e reentrante
```

O SQLite fica em `$XDG_DATA_HOME/ayo-desk/desk.sqlite3`, com permissão 0600. O esquema evolui por migrações numeradas em `migrations.py`: cada uma roda numa transação e só avança `user_version` se terminar inteira. Migrações publicadas nunca são editadas; mudanças novas entram como a próxima da lista. A biblioteca musical separa arquivos descobertos automaticamente de arquivos adicionados manualmente. Uma busca só altera o banco depois de terminar; se a pasta falhar, a última biblioteca continua disponível.

Integrações do sistema ficam fora da camada visual. As operações longas usam callbacks no contexto principal do GLib, e comandos que ainda precisam de uma ferramenta existente (`pactl`) são executados como vetores de argumentos, sem shell.
