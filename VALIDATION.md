# Verificação desta entrega

- `make check`: 22 testes passaram, incluindo decodificação de áudio, pausa, seek e fim da reprodução com uma saída silenciosa. A biblioteca por pasta foi testada com subpastas, extensões em maiúsculas, links, troca de pasta, atualização após reinício, migração de playlists antigas, remoções e cancelamento.
- `make native-check`: 25.000 entradas de teste no parser C passaram com AddressSanitizer e UndefinedBehaviorSanitizer, sem erro de memória detectado. Esse comando foi executado fora do sandbox porque o LeakSanitizer não funciona sob o isolamento usado nesta sessão.
- `python3 scripts/smoke.py`: seis telas abertas no Hyprland, zero exceções. A central contém somente rede, áudio e Bluetooth. Música, calculadora e calendário foram abertos em processos de teste separados, com identidade própria, título Ayo e sem a barra lateral da central. Rede, áudio e Bluetooth consultaram os serviços reais; calendário e calculadora usaram um banco temporário.
- Sete atalhos instalados no menu do usuário e validados com `desktop-file-validate`.
- `python3 scripts/smoke.py --only music`: leitura automática da pasta salva e troca de origem testadas na interface com arquivos temporários, incluindo o tratamento do resultado do seletor de pasta. A lista e o caminho exibido foram atualizados sem reproduzir áudio.

Os pacotes `gst-plugins-base`, `gst-plugins-good`, `gst-libav` e `dnsmasq` foram encontrados instalados na verificação desta atualização. A pendência de dependências da primeira entrega está resolvida.

Conectar/desconectar o Wi-Fi real, compartilhar pela RJ45, alterar volumes e parear aparelhos físicos não foram acionados durante os testes. Os perfis gerados foram validados pelo libnm; a verificação física dessas operações depende dos dispositivos e das permissões locais.
