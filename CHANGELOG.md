# Changelog

## Não lançado — Ayo Música completo (em andamento)

- Biblioteca por tags com mutagen, inferência pelo nome do arquivo/pasta e leitura incremental.
- Capas em cache, visões de álbuns, artistas (com colaboradores), gêneros e pastas; busca sem acentos.
- Nova interface: barra lateral adaptável, barra do player, Tocando agora, fila e propriedades.
- Motor de reprodução com gapless, fila com tocar a seguir, aleatório sem repetição e repetir uma.
- Sessão retomada ao abrir, contagem de reproduções/pulos e favoritas.
- Migrações versionadas do banco de dados e tabela de preferências.
- MPRIS (teclas de mídia, playerctl, Waybar), notificações de troca de música e reprodução em segundo plano.
- Fila com arrastar para reordenar e álbuns aleatórios; atalhos de teclado; timer de sono com fade-out.
- Velocidade de 0,5× a 2× mantendo o tom, escolha do dispositivo de saída e retomada de faixas longas.
- Abrir arquivos pelo gerenciador de arquivos ou pela linha de comando.
- Playlists locais: criar, renomear, excluir, adicionar pelo menu ou arrastando, reordenar, salvar a fila,
  importar M3U/M3U8/PLS e exportar M3U8.
- Som: nivelamento de volume (tags ReplayGain ou medição própria em segundo plano, modos faixa/álbum/automático),
  equalizador de 10 bandas com presets, crossfade que respeita álbuns em ordem, pausa suave,
  barra de progresso em forma de onda e visualizador de espectro.
- Visualizador estilo CAVA embutido (usa o `cava` instalado; barras, espelhado, onda ou pontos; faixa opcional
  acima do player) e aviso de fontes faltando para títulos em outras línguas.
- Organizar biblioteca: identifica músicas pelo nome/tags (Deezer), pelo som (SongRec/Shazam) e pelo MusicBrainz;
  completa artista, álbum, ano, faixa, gênero, ISRC e troca miniaturas de vídeo pela capa oficial. Grava sozinho
  quando há certeza, manda o resto para revisão e guarda backup para desfazer. Identificar álbum inteiro.
- Corrigido: seek durante a troca gapless de faixa podia travar o player.

## 0.1.2

- Ayo Desk separado dos aplicativos Ayo Música, Ayo Calculadora e Ayo Calendário.
- Biblioteca de música por pasta, incluindo subpastas, atualização e persistência.
- Tema preto e branco seguindo o modo claro/escuro do sistema.
- Testes de integração de serviços, player, persistência e interface.

## 0.1.1

- Primeira suíte funcional com rede, áudio, Bluetooth, calendário, calculadora e player.
