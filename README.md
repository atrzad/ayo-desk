# Ayo Desk

Ferramentas nativas para Arch Linux, feitas em **Python + GTK4/libadwaita**, com uma **biblioteca C** usada pela calculadora. Funcionam no Hyprland/Wayland e também em sessões X11.

**Ayo Desk** reúne apenas rede, áudio/microfone e Bluetooth. **Ayo Música**, **Ayo Calculadora** e **Ayo Calendário** são aplicativos independentes, com seus próprios atalhos, janelas e processos. Fechar a central não fecha esses aplicativos.

| Ferramenta | Recursos |
| --- | --- |
| Rede | Buscar e filtrar Wi-Fi, conectar, desconectar, redes ocultas, WPA/WPA2/WPA3/OWE, perfis salvos, última utilização, histórico local e compartilhar a internet pela Ethernet/RJ45. |
| Áudio e microfone | Volume e mute de saídas/entradas, dispositivo padrão, volume por aplicativo, trocar a saída/entrada de aplicativos e perfis de placas/fones. |
| Bluetooth | Ligar/desligar adaptadores, busca de 30 segundos, parear com confirmação/PIN, conectar, desconectar, confiar, esquecer e bateria quando informada pelo dispositivo. |
| Calendário | Navegar por datas, criar/editar/excluir compromissos com horário e notas, marcar dias ocupados e persistir os dados localmente. |
| Calculadora | Operações básicas, potências, porcentagem, parênteses, funções científicas, histórico e copiar resultado. Parser em C, sem `eval`. |
| Música | Biblioteca por tags (com leitura do nome do arquivo/pasta quando faltam), capas, álbuns, artistas, gêneros e pastas; busca sem acentos; fila com tocar a seguir; gapless; aleatório sem repetição; repetir todas/uma; retomada da sessão; contagem de reproduções; favoritas; pasta acompanhada automaticamente; arrastar e soltar. |

## Dependências

Use o Python do sistema; PyGObject vem do pacote `python-gobject`, sem necessidade de pip ou venv.

```sh
sudo pacman -S --needed base-devel python python-gobject gtk4 libadwaita libnm networkmanager libpulse bluez gstreamer gst-plugins-base gst-plugins-good gst-libav dnsmasq python-mutagen songrec
# Fontes para títulos em outras línguas e emojis (recomendado):
sudo pacman -S --needed noto-fonts noto-fonts-cjk noto-fonts-emoji noto-fonts-extra
```

O controle de áudio requer um servidor PulseAudio ou PipeWire com `pipewire-pulse` já configurado. O Ayo não substitui automaticamente seu servidor de áudio ou gerenciador de rede. NetworkManager e bluetooth.service precisam estar ativos; o aplicativo mostra quando estão indisponíveis. No Hyprland, mantenha um agente de autenticação Polkit funcionando para operações que exigem autorização.

## Compilar e abrir

```sh
cd /home/ayo/Projects/ayo-desk
make
python3 -m ayo_desk
```

Abrir cada aplicativo separadamente:

```sh
python3 -m ayo_desk --standalone --page network
python3 -m ayo_desk --standalone --page audio
python3 -m ayo_desk --standalone --page bluetooth
python3 -m ayo_desk --page calendar
python3 -m ayo_desk --page calculator
python3 -m ayo_desk --page music
```

Para rede, áudio e Bluetooth, `--page` seleciona uma seção na central; `--standalone` abre o controle em uma janela própria. Música, calculadora e calendário sempre abrem como aplicativos independentes, inclusive quando chamados por `--page`. Cada aplicativo reutiliza apenas sua própria janela. `Ctrl+Q` fecha a janela. O botão no cabeçalho alterna entre tema claro e escuro.

O aplicativo usa os estilos nativos do libadwaita. Uma variável global `GTK_THEME` é ignorada dentro do processo do Ayo para evitar que temas GTK3 causem sobreposição de textos e controles.

## Instalar para seu usuário

```sh
make install
```

Instala uma cópia em `~/.local/share/ayo-desk/app`, o executável `~/.local/bin/ayo-desk` e sete entradas de menu: **Ayo Desk**, **Ayo Rede**, **Ayo Áudio e microfone**, **Ayo Bluetooth**, **Ayo Calendário**, **Ayo Calculadora** e **Ayo Música**. Não precisa de sudo. Para aplicar alterações do código, execute `make install` novamente.

Os três aplicativos independentes também têm comandos próprios:

```sh
~/.local/bin/ayo-musica
~/.local/bin/ayo-calculadora
~/.local/bin/ayo-calendario
```

Exemplo opcional de atalho no Hyprland:

```ini
bind = SUPER, N, exec, ~/.local/bin/ayo-desk --standalone --page network
```

`make uninstall` remove apenas os aplicativos e atalhos; preserva os dados pessoais.

## Compartilhar Wi-Fi pela RJ45

1. Conecte o computador à internet pelo Wi-Fi.
2. Conecte um cabo Ethernet entre ele e o outro dispositivo.
3. Em **Rede → Compartilhar internet pela RJ45**, clique em **Compartilhar** na porta correta.
4. Deixe o outro dispositivo obter endereço IP e DNS automaticamente (DHCP).
5. Use **Parar** para encerrar o compartilhamento; para voltar a usar um perfil cabeado anterior, selecione-o em **Conexões salvas**.

O Ayo cria um perfil exclusivo para a interface, com IPv4 `shared`, sem ativação automática. O NetworkManager fornece DHCP/DNS/NAT usando sua própria instância do dnsmasq; **não é necessário habilitar dnsmasq.service**. A porta que fornece a internet não pode ser escolhida como destino. Um firewall pode exigir uma regra para o encaminhamento; o aplicativo não reescreve as regras do seu firewall.

## Ayo Música

1. Abra **Ayo Música** e escolha a pasta da sua coleção (ou use `~/Music` direto na tela inicial).
2. O Ayo busca os arquivos na pasta e nas subpastas, em segundo plano, lê as tags e as capas e guarda tudo no banco local. Nas próximas vezes só relê o que mudou.
3. Enquanto o app está aberto, a pasta é acompanhada: músicas novas, removidas ou editadas aparecem sozinhas (dá para desligar em **Preferências**).

Quando o arquivo não tem tags, o Ayo usa o nome e a pasta: `Album - Gêmeos/01 - Esperando Você.mp3` vira faixa 1, "Esperando Você", do álbum "Gêmeos". Ele remove o id do YouTube (`[1rY6FxzenSo]`), marcações como `(Official Video)` e `(MP3_320K)`, e desfaz as trocas de caracteres feitas pelo yt-dlp (`：` → `:`). Em **Propriedades**, os campos deduzidos assim aparecem marcados com •.

Na barra lateral ficam Tocando agora, Fila, Músicas, Álbuns, Artistas, Gêneros e Pastas. Clique duas vezes numa música para tocar a lista a partir dela; o botão direito abre tocar a seguir, adicionar à fila, ir para o álbum/artista, propriedades, abrir pasta e remover. `Ctrl+F` busca em título, artista, álbum, gênero, ano e nome do arquivo, sem diferenciar acentos. Arquivos e pastas podem ser arrastados para a janela.

**Integração com o sistema.** O Ayo Música publica o MPRIS (`org.mpris.MediaPlayer2.ayo_musica`): as teclas de mídia do Hyprland via `playerctl`, a Waybar e outros controles mostram título, artista e capa, e controlam tocar/pausar, próxima, anterior, posição, volume, repetir e aleatório. Fechar a janela com música tocando deixa o player rodando em segundo plano; abrir o Ayo Música de novo traz a janela de volta e `Ctrl+Q` encerra de vez. Quando a janela não está em foco, cada música nova aparece numa notificação com a capa. Arquivos abertos pelo gerenciador de arquivos ("Abrir com Ayo Música") ou passados na linha de comando (`ayo-musica faixa.mp3`) tocam na hora, sem entrar na biblioteca.

**Atalhos.** `Espaço` toca/pausa, `Ctrl+←/→` anterior/próxima, `Shift+←/→` volta/avança 5 s, `Ctrl+↑/↓` volume, `M` silencia, `S` ordem aleatória, `R` repetir, `Ctrl+F` busca, `Ctrl+1…5` troca de tela, `Ctrl+,` preferências e `Ctrl+?` mostra todos os atalhos.

**Playlists.** Crie playlists em **Nova playlist** na barra lateral (ou no menu principal), pelo botão direito numa ou várias músicas (**Adicionar à playlist → Nova playlist…**) ou salvando a fila atual. Para adicionar músicas, use **Adicionar à playlist** no menu de contexto ou arraste as músicas de qualquer lista até o nome da playlist na barra lateral; músicas repetidas são ignoradas. Dentro da playlist, arraste as linhas para mudar a ordem e use **Remover desta playlist** no botão direito. O menu ⋮ da playlist renomeia, exclui e exporta como M3U8 (com caminhos relativos, que funcionam em outros players); **Importar playlist (M3U)…** lê M3U, M3U8 e PLS de outros programas. Arquivos da playlist que saíram da biblioteca continuam na lista, esmaecidos.

**Identificar músicas e completar os metadados.** Em **Ferramentas → Organizar biblioteca** o Ayo mostra o que está faltando (artista, álbum, ano, número da faixa e capas que são miniaturas de vídeo) e identifica as músicas:
1. Primeiro pelo nome e pelas tags. Ele limpa o que os downloaders do YouTube deixam, como "Artista - Topic", "(Official Video)", "(MP3_160K)" e `_` no lugar de `:` ou `'`, e busca no **Deezer**.
2. Se não tiver certeza, reconhece **pelo som** com o **SongRec** (cliente livre do Shazam, que envia só a impressão digital do áudio).
3. Como reserva, usa o **MusicBrainz**, com capa do Cover Art Archive.

O Deezer completa título, artista, álbum, artista do álbum, data, faixa, gênero, ISRC e a **capa oficial em 1000×1000**. Quando título, artista e duração batem (±4 s), a correção é **gravada sozinha no arquivo**. Coletâneas, versões diferentes e durações diferentes vão para **Para revisar**, com antes e depois, a capa nova ao lado da antiga, a confiança e **Outras opções / Buscar**. Antes de gravar, as tags e a capa antigas são guardadas: **Desfazer** funciona por música ou para o lote inteiro. Também dá para usar **Identificar** no menu de contexto e o botão de **Identificar álbum** na página do álbum, que casa as faixas pelo título e pela duração. Músicas novas na pasta são identificadas sozinhas (desligável em **Preferências → Metadados**). O áudio, os nomes dos arquivos e as letras nunca são alterados.

**Som.** Em **Preferências → Som**: nivelamento de volume (usa as tags ReplayGain quando existem e mede o resto em segundo plano; no modo automático, álbuns tocados em ordem mantêm a dinâmica original), pré-amplificação, crossfade de 0 a 12 s (faixas seguidas do mesmo álbum continuam emendadas sem intervalo) e pausa suave. O **Equalizador** (na tela Tocando agora ou no botão de relógio) tem 10 bandas, presets e presets próprios. A barra do player mostra a forma de onda da música; clique ou arraste para ir a qualquer ponto. O botão **Visualizador** em Tocando agora mostra as barras do som. Com o `cava` instalado, o Ayo roda o CAVA por baixo (reage a todo o som do computador); sem ele, usa o próprio espectro, sincronizado com o que se ouve. Em Preferências → Som dá para escolher estilo (barras, espelhado, onda, pontos), número de barras e uma faixa fina acima da barra do player. Se algum título tiver caracteres sem fonte instalada (japonês, árabe, emoji...), o app avisa qual pacote `noto-fonts-*` instalar.

**Fila, velocidade e timer.** Na **Fila**, arraste as próximas músicas para reordenar e escolha tocar na ordem, com músicas aleatórias ou com álbuns aleatórios (cada álbum inteiro, em ordem). O botão de relógio na barra do player ajusta a velocidade de 0,5× a 2× sem mudar o tom da voz, liga o timer de sono (15 min a 1h30, fim da música ou fim da fila, com o volume diminuindo aos poucos) e escolhe o dispositivo de saída. Faixas com mais de 20 minutos, como audiolivros e podcasts, voltam de onde pararam.

A fila, a música atual e a posição ficam salvas: ao abrir de novo, a última música aparece pausada no ponto onde parou. Uma reprodução só conta depois de metade da música ou 4 minutos; pular antes disso conta como pulo. São reconhecidas extensões como MP3, FLAC, OGG, OPUS, WAV, M4A, AAC e WMA, conforme os codecs instalados. Pastas ocultas e atalhos para diretórios não são percorridos.

## Dados e limites

- Eventos, biblioteca de músicas, estatísticas e histórico ficam em `$XDG_DATA_HOME/ayo-desk/desk.sqlite3` ou `~/.local/share/ayo-desk/desk.sqlite3`, com permissão `0600`. Os arquivos de música permanecem onde você os colocou; removê-los da biblioteca não os apaga do disco.
- Senhas Wi-Fi são enviadas pela API do NetworkManager e guardadas no perfil administrado por ele. Não são passadas como argumentos de processos nem gravadas no histórico do Ayo.
- O histórico do Ayo registra conexões concluídas por ele; os perfis do NetworkManager mostram também a última utilização conhecida. Não é um registro completo de todas as conexões feitas antes da instalação.
- Novas redes pessoais WPA/WPA2/WPA3, OWE e abertas são configuráveis. Redes corporativas 802.1X e legadas precisam de um perfil previamente configurado no NetworkManager. VPNs existentes aparecem nos perfis salvos; não há editor de VPN/certificados nesta versão.
- Calendário local, sem sincronização de contas, recorrência ou lembretes em segundo plano. Os horários são locais.
- O player reproduz arquivos locais nos formatos cobertos pelos codecs GStreamer instalados. As capas ficam em cache em `$XDG_CACHE_HOME/ayo-desk/covers`. Sem o `python-mutagen`, o app usa apenas o nome do arquivo e da pasta.
- Trigonometria em radianos. `%` significa dividir por 100: `200*10% = 20`; `200+10% = 200.1`. Funções: `sqrt`, `sin`, `cos`, `tan`, `asin`, `acos`, `atan`, `ln`, `log`, `exp`, `abs`, `floor`, `ceil`; constantes `pi`/`π` e `e`. Resultados usam precisão de `double`.

## Verificação e arquitetura

```sh
make check
python3 scripts/smoke.py --snapshots /tmp/ayo-desk-preview
```

O teste gráfico abre as seis telas com um banco temporário e consulta os serviços reais. Não muda conexões, pareia aparelhos ou altera o volume do sistema. Os testes unitários validam o parser C, persistência, perfis de Wi-Fi/compartilhamento e limites das integrações.

Para diagnosticar uma instalação sem alterar o sistema:

```sh
make doctor
```

O código-fonte completo está organizado neste repositório, com o plano de evolução em [`docs/ROADMAP.md`](docs/ROADMAP.md), a arquitetura em [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) e instruções para contribuições em [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md).

- `native/calc.c`: parser matemático reentrante em C, com limite de tamanho/profundidade, chamado por `ctypes`.
- `ayo_desk/network.py`: libnm, operações assíncronas, confirmação de estado antes de registrar sucesso.
- `ayo_desk/bluetooth.py`: BlueZ via D-Bus, agente de pareamento restrito ao aplicativo.
- `ayo_desk/tasks.py`: comandos `pactl` sem shell, executados fora da thread da interface.
- `ayo_desk/player.py`: GStreamer `playbin`, reprodução e eventos assíncronos.
- `ayo_desk/core.py`: SQLite parametrizado e ligação com a biblioteca C.

Referências: [API libnm](https://networkmanager.dev/docs/libnm/latest/NMClient.html), [API de pareamento BlueZ](https://bluez.readthedocs.io/en/latest/agent-api/), [GStreamer playbin](https://gstreamer.freedesktop.org/documentation/playback/playbin.html), [compartilhamento no ArchWiki](https://wiki.archlinux.org/title/NetworkManager#Sharing_internet_connection_over_Ethernet).
