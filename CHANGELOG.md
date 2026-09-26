# Changelog

## Não lançado — Ayo Kanban

- Novo aplicativo independente **Ayo Kanban** (`ayo-kanban`), com ícone próprio em preto e branco.
- Vários quadros; colunas com limite de cartões, coluna de concluídos, reordenação e exclusão sem perder cartões.
- Arrastar e soltar com rolagem automática nas bordas, atalhos `Alt+setas` e menu *Mover para*.
- Captura rápida com `#etiqueta`, `!`/`!!`/`!!!` e `@hoje`, `@amanhã`, `@sexta`, `@25/12`.
- Prazos, prioridade, etiquetas, notas e lista de tarefas por cartão; busca sem acentos.
- Arquivo com desfazer, exportação/importação JSON e exportação Markdown.
- Dados em arquivo próprio (`kanban.sqlite3`), com migrações versionadas independentes do `desk.sqlite3`.

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

## 0.1.2

- Ayo Desk separado dos aplicativos Ayo Música, Ayo Calculadora e Ayo Calendário.
- Biblioteca de música por pasta, incluindo subpastas, atualização e persistência.
- Tema preto e branco seguindo o modo claro/escuro do sistema.
- Testes de integração de serviços, player, persistência e interface.

## 0.1.1

- Primeira suíte funcional com rede, áudio, Bluetooth, calendário, calculadora e player.
