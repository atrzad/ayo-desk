# Contribuindo

Use o Python do sistema e as bibliotecas do Arch. Antes de enviar uma mudança:

```sh
make check
make native-check
python3 scripts/smoke.py
```

Não inclua `build/`, bancos SQLite, arquivos de música ou capturas de tela. Mudanças de dados devem aceitar bancos já existentes; mudanças de integração precisam de um teste que cubra erro e cancelamento. Para uma nova função de tela, mantenha a operação fora da thread GTK e adicione uma linha ao roadmap quando ela mudar o escopo do aplicativo.
