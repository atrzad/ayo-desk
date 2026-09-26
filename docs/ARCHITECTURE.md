# Arquitetura

O repositório contém uma suíte de aplicativos com componentes compartilhados:

```text
ayo_desk/
├── app.py              central Ayo Desk e janelas independentes
├── network.py          libnm e perfis NetworkManager
├── bluetooth.py        BlueZ via D-Bus
├── tasks.py            operações assíncronas e pactl argumentado
├── music/              Ayo Música
│   ├── library.py      descoberta segura de arquivos e leitura incremental
│   ├── tags.py         tags (mutagen) + inferência pelo nome do arquivo/pasta
│   ├── covers.py       cache de capas endereçado pelo conteúdo
│   ├── db.py           metadados, estatísticas e consultas
│   ├── queue.py        fila, aleatório sem repetição e modos de repetição
│   ├── engine.py       GStreamer playbin com gapless
│   └── ui/             interface GTK4/libadwaita (modelo, visões, barra do player)
├── kanban/             Ayo Kanban
│   ├── schema.py       kanban.sqlite3 e suas migrações versionadas, separadas do desk.sqlite3
│   ├── board.py        quadros, colunas, cartões e listas; captura rápida, busca e prazos
│   ├── exchange.py     exportação/importação JSON e Markdown, validadas antes de gravar
│   └── ui/             quadro, colunas com arrastar e soltar, cartões e editores
├── migrations.py       esquema SQLite versionado (PRAGMA user_version)
├── core.py             SQLite, configurações e ligação ctypes com a biblioteca C
└── *_page.py           telas GTK4/libadwaita
native/calc.c           parser matemático limitado e reentrante
```

O SQLite fica em `$XDG_DATA_HOME/ayo-desk/desk.sqlite3`, com permissão 0600. O esquema evolui por migrações numeradas em `migrations.py`: cada uma roda numa transação e só avança `user_version` se terminar inteira. Migrações publicadas nunca são editadas; mudanças novas entram como a próxima da lista. A biblioteca musical separa arquivos descobertos automaticamente de arquivos adicionados manualmente. Uma busca só altera o banco depois de terminar; se a pasta falhar, a última biblioteca continua disponível.

Integrações do sistema ficam fora da camada visual. As operações longas usam callbacks no contexto principal do GLib, e comandos que ainda precisam de uma ferramenta existente (`pactl`) são executados como vetores de argumentos, sem shell.

O Ayo Kanban guarda tudo em `kanban.sqlite3`, com migrações próprias em `kanban/schema.py` e chaves estrangeiras ativas, para evoluir sem depender da numeração das migrações do `desk.sqlite3`. `board.py` não depende de GTK e mantém as posições dos cartões ativos de cada coluna contíguas (0..n-1); mover um cartão reescreve só as colunas envolvidas numa transação. Cartões arquivados ficam na coluna de origem e voltam ao fim dela. A importação valida o arquivo inteiro antes e grava todos os quadros numa única transação. A interface reconstrói as colunas a partir do banco depois de cada ação, preservando a rolagem e o foco.

No Ayo Música, a lógica (`tags`, `library`, `db`, `queue`, `engine`) não depende de GTK e é testada diretamente. A interface mantém toda a biblioteca em memória como objetos `Track` e deriva álbuns, artistas, gêneros e pastas em `ui/model.py`. As listas usam `Gtk.ColumnView`/`Gtk.GridView`, que reciclam widgets. A leitura de tags roda em uma thread de trabalho, e só o resultado volta para o banco na thread principal.
