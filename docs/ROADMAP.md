# Roadmap do Ayo Desk

O objetivo é chegar a uma suíte completa para o uso diário no Arch Linux sem esconder operações do sistema, sem guardar segredos no aplicativo e sem travar a interface.

## Estado atual

- **Ayo Desk:** Wi-Fi, conexões salvas, histórico local, áudio/microfone e Bluetooth.
- **Ayo Música:** pasta persistente com subpastas, arquivos avulsos, player GStreamer, busca, volume, seek, aleatório e repetição.
- **Ayo Calculadora:** parser seguro em C, funções científicas, histórico e cópia do resultado.
- **Ayo Calendário:** eventos locais, notas, edição, exclusão e marcação de dias ocupados.
- **Ayo Kanban:** vários quadros, colunas com limite, arrastar e soltar, captura rápida, prazos, prioridade, etiquetas, listas de tarefas, busca, arquivo e exportação/importação JSON e Markdown.
- Tema monocromático seguindo o modo claro/escuro do sistema.

## Fase 1 — fundação (em andamento)

1. Repositório próprio, documentação, testes reproduzíveis e verificação de dependências.
2. Migrações versionadas do SQLite, exportação/backup e recuperação de dados.
3. Configurações comuns: tema do sistema, idioma, diretório de dados e atalhos.
4. Erros consistentes, logs opt-in e operação assíncrona em todas as telas.
5. Empacotamento Arch Linux e atualização sem apagar configurações do usuário.

## Fase 2 — experiência diária

- **Música:** tags artista/álbum, capas, playlists, fila, atalhos multimídia, MPRIS, monitoramento da pasta e retomada da última faixa.
- **Kanban:** prazos dos cartões no Ayo Calendário, lembretes, cartões recorrentes, capa/cor monocromática por etiqueta, comando `ayo-kanban add` para capturar pelo Hyprland/wofi e reordenar colunas arrastando.
- **Calendário:** recorrência, lembretes, busca, categorias, exportar/importar ICS e fuso horário explícito.
- **Calculadora:** unidades, conversão, memória, porcentagem contextual, base binária/hexadecimal e histórico exportável.
- **Rede:** VPN, hotspot Wi-Fi, importação/exportação de perfis e diagnóstico de DNS/rota.
- **Áudio:** PipeWire nativo, medidores de nível, perfis por aplicativo, proteção contra volume perigoso e atalhos.
- **Bluetooth:** bateria, auto-reconexão opcional, perfis de áudio e histórico de pareamento.

## Fase 3 — integração do desktop

- Notificações nativas e ações rápidas.
- MPRIS para o player e atalhos de mídia.
- Indicadores opcionais para Waybar/Hyprland.
- D-Bus bem definido para abrir uma seção e executar ações autorizadas.
- Traduções e acessibilidade de teclado/leitor de tela.

## Critérios para chamar um app de “full”

Cada recurso precisa ter uma ação reversível, estado persistente, mensagens de erro compreensíveis, teste automatizado e uma forma de desligá-lo. A interface não deve executar shell com entrada do usuário; integrações devem usar libnm, BlueZ, PipeWire/PulseAudio ou GStreamer diretamente quando houver API disponível.
