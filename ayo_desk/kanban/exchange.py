"""Open formats for Ayo Kanban: JSON to back up or move boards, Markdown to read or share them."""
import datetime as dt
import json
import os

from .board import (DEFAULT_COLUMNS, PRIORITIES, clean_checklist, clean_limit, clean_name, clean_notes,
                    clean_priority, clean_tags, clean_title, due_label, now, parse_due)

FORMAT = "ayo-kanban"
VERSION = 1
MAX_BYTES = 20 * 1024 * 1024


def export_boards(kanban, board_ids):
    boards = []
    for board_id in board_ids:
        board = kanban.board(board_id)
        cards = kanban.cards(board_id) + kanban.cards(board_id, archived=True)
        columns = []
        for column in kanban.columns(board_id):
            columns.append({
                "name": column["name"], "wip_limit": column["wip_limit"], "done": bool(column["done"]),
                "cards": [{"title": c["title"], "notes": c["notes"], "priority": c["priority"], "due": c["due"],
                           "tags": c["tags"], "archived": bool(c["archived"]), "created": c["created"],
                           "updated": c["updated"], "done_at": c["done_at"],
                           "checklist": [{"text": i["text"], "done": bool(i["done"])} for i in kanban.checklist(c["id"])]}
                          for c in cards if c["column_id"] == column["id"]]})
        boards.append({"name": board["name"], "columns": columns})
    return {"format": FORMAT, "version": VERSION, "exported": now(), "boards": boards}


def dumps(data):
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def loads(text):
    """Parse and validate a whole file before anything is written, so a bad file changes nothing."""
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ValueError("O arquivo é grande demais para um quadro (limite de 20 MB).")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"O arquivo não é um JSON válido (linha {exc.lineno}).") from None
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError("Este arquivo não foi exportado pelo Ayo Kanban.")
    if not isinstance(data.get("version"), int) or data["version"] > VERSION:
        raise ValueError("Este arquivo foi criado por uma versão mais nova do Ayo Kanban.")
    boards = data.get("boards")
    if not isinstance(boards, list) or not boards:
        raise ValueError("O arquivo não contém quadros.")
    return [normalize_board(board, n) for n, board in enumerate(boards, start=1)]


def read(path):
    """Read and validate an export file; runs off the GTK thread."""
    if os.path.getsize(path) > MAX_BYTES:
        raise ValueError("O arquivo é grande demais para um quadro (limite de 20 MB).")
    try:
        with open(path, encoding="utf-8") as source:
            text = source.read()
    except UnicodeDecodeError:
        raise ValueError("O arquivo não está em UTF-8.") from None
    return loads(text)


def save(path, text):
    """Write next to the destination and rename, so an interrupted export never leaves half a file."""
    temporary = f"{path}.part"
    with open(temporary, "w", encoding="utf-8") as output:
        output.write(text)
    os.replace(temporary, path)


def normalize_board(board, number):
    where = f"quadro {number}"
    try:
        if not isinstance(board, dict):
            raise ValueError("formato inesperado")
        columns = board.get("columns") or [{"name": name, "done": done} for name, done in DEFAULT_COLUMNS]
        if not isinstance(columns, list):
            raise ValueError("as colunas precisam ser uma lista")
        return {"name": clean_name(board.get("name", ""), "do quadro"),
                "columns": [normalize_column(column) for column in columns]}
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ValueError(f"Arquivo inválido no {where}: {exc}") from None


def normalize_column(column):
    cards = column.get("cards", [])
    if not isinstance(cards, list):
        raise ValueError("os cartões precisam ser uma lista")
    return {"name": clean_name(column.get("name", ""), "da coluna"),
            "wip_limit": clean_limit(column.get("wip_limit", 0)),
            "done": bool(column.get("done", False)),
            "cards": [normalize_card(card) for card in cards]}


def normalize_card(card):
    tags = card.get("tags", [])
    checklist = card.get("checklist", [])
    if not isinstance(tags, list) or not isinstance(checklist, list):
        raise ValueError("etiquetas e listas precisam ser listas")
    stamp = now()
    return {"title": clean_title(card.get("title", "")),
            "notes": clean_notes(card.get("notes", "")),
            "priority": clean_priority(card.get("priority", 0)),
            "due": parse_due(card.get("due")),
            "tags": clean_tags(tags),
            "archived": bool(card.get("archived", False)),
            "created": timestamp(card.get("created")) or stamp,
            "updated": timestamp(card.get("updated")) or stamp,
            "done_at": timestamp(card.get("done_at")),
            "checklist": clean_checklist(checklist)}


def timestamp(value):
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value)).isoformat(timespec="seconds")
    except ValueError:
        return None


def to_markdown(kanban, board_id, today=None):
    """Readable Markdown: one section per column, a task list item per card."""
    board = kanban.board(board_id)
    cards = kanban.cards(board_id)
    lines = [f"# {board['name']}", "",
             f"_Exportado pelo Ayo Kanban em {(today or dt.date.today()).strftime('%d/%m/%Y')}._", ""]
    for column in kanban.columns(board_id):
        own = [c for c in cards if c["column_id"] == column["id"]]
        limit = f" · limite {column['wip_limit']}" if column["wip_limit"] else ""
        lines += [f"## {column['name']} ({len(own)}{limit})", ""]
        for card in own:
            details = [f"**{markdown_text(card['title'])}**"]
            if card["priority"]:
                details.append(f"prioridade {PRIORITIES[card['priority']].lower()}")
            if card["due"]:
                text, _state = due_label(card["due"], today, bool(column["done"]))
                details.append(f"prazo {dt.date.fromisoformat(card['due']).strftime('%d/%m/%Y')}"
                               + (" (atrasado)" if text.startswith("Atrasado") else ""))
            details += [f"#{tag}" for tag in card["tags"]]
            lines.append(f"- [{'x' if column['done'] else ' '}] " + " · ".join(details))
            lines += [f"  {line}".rstrip() for line in card["notes"].splitlines()]
            lines += [f"  - [{'x' if item['done'] else ' '}] {markdown_text(item['text'])}"
                      for item in kanban.checklist(card["id"])]
        if not own:
            lines.append("_Sem cartões._")
        lines.append("")
    return "\n".join(lines)


def markdown_text(text):
    for char in "\\*_`[]":
        text = text.replace(char, "\\" + char)
    return text
