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
| Música | Pasta de músicas com subpastas, atualização da biblioteca, arquivos avulsos, busca, reproduzir/pausar, anterior/próxima, seek, volume, metadados, aleatório e repetição da biblioteca. |

## Dependências

Use o Python do sistema; PyGObject vem do pacote `python-gobject`, sem necessidade de pip ou venv.

```sh
sudo pacman -S --needed base-devel python python-gobject gtk4 libadwaita libnm networkmanager libpulse bluez gstreamer gst-plugins-base gst-plugins-good gst-libav dnsmasq python-mutagen
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

## Escolher a pasta de músicas

1. Abra **Ayo Música** e clique em **Escolher pasta**.
2. Selecione a pasta da sua coleção e confirme em **Usar esta pasta**.
3. O aplicativo busca os arquivos de áudio nela e nas subpastas, em segundo plano.

A pasta fica salva e é verificada novamente ao abrir o player. Use o botão de atualização ao lado do caminho para ler músicas novas, removidas ou movidas enquanto o aplicativo está aberto. **Trocar pasta** muda a origem da biblioteca; o botão **+** permite continuar adicionando arquivos avulsos.

Trocar de pasta substitui as faixas descobertas automaticamente e mantém os arquivos adicionados manualmente. A faixa em reprodução pode continuar tocando durante a atualização. Remover uma faixa da biblioteca não apaga o arquivo e a mantém oculta nas buscas seguintes da mesma pasta; adicioná-la novamente pelo **+** restaura sua presença.

São reconhecidas extensões como MP3, FLAC, OGG, OPUS, WAV, M4A, AAC e WMA, inclusive em maiúsculas, conforme os codecs instalados. Pastas ocultas e atalhos para diretórios não são percorridos. Se uma pasta estiver indisponível, a última biblioteca fica preservada para tentar novamente.

## Dados e limites

- Eventos, playlists e histórico ficam em `$XDG_DATA_HOME/ayo-desk/desk.sqlite3` ou `~/.local/share/ayo-desk/desk.sqlite3`, com permissão `0600`. Os arquivos de música permanecem onde você os colocou; removê-los da biblioteca não os apaga do disco.
- Senhas Wi-Fi são enviadas pela API do NetworkManager e guardadas no perfil administrado por ele. Não são passadas como argumentos de processos nem gravadas no histórico do Ayo.
- O histórico do Ayo registra conexões concluídas por ele; os perfis do NetworkManager mostram também a última utilização conhecida. Não é um registro completo de todas as conexões feitas antes da instalação.
- Novas redes pessoais WPA/WPA2/WPA3, OWE e abertas são configuráveis. Redes corporativas 802.1X e legadas precisam de um perfil previamente configurado no NetworkManager. VPNs existentes aparecem nos perfis salvos; não há editor de VPN/certificados nesta versão.
- Calendário local, sem sincronização de contas, recorrência ou lembretes em segundo plano. Os horários são locais.
- O player reproduz arquivos locais nos formatos cobertos pelos codecs GStreamer instalados. Não inclui streaming de serviços ou integração MPRIS nesta versão. Fechar a janela encerra a reprodução.
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
