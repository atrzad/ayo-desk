"""Boards, ordered columns, cards and checklists in kanban.sqlite3; no graphical dependencies.

Active cards of a column always keep contiguous positions 0..n-1, so a card's position is also its index.
Archived cards keep their column and return to the end of it when restored.
"""
import datetime as dt
import json
import unicodedata

from .schema import connect

PRIORITIES = ("Nenhuma", "Baixa", "Média", "Alta")
DEFAULT_COLUMNS = (("A fazer", False), ("Fazendo", False), ("Feito", True))
DEFAULT_BOARD = "Meu quadro"
MAX_NAME = 80
MAX_TITLE = 300
MAX_NOTES = 20000
MAX_TAG = 40
MAX_TAGS = 20
MAX_ITEMS = 200
MAX_WIP = 999
WEEKDAYS = {"segunda": 0, "terca": 1, "quarta": 2, "quinta": 3, "sexta": 4, "sabado": 5, "domingo": 6}


def now():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def fold(text):
    """Lowercase without accents, so "Reunião" matches "reuniao"."""
    decomposed = unicodedata.normalize("NFKD", str(text))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def clean_name(text, what):
    text = " ".join(str(text).split())
    if not text:
        raise ValueError(f"Informe o nome {what}.")
    if len(text) > MAX_NAME:
        raise ValueError(f"O nome {what} pode ter até {MAX_NAME} caracteres.")
    return text


def clean_title(text):
    text = " ".join(str(text).split())
    if not text:
        raise ValueError("Informe o título do cartão.")
    if len(text) > MAX_TITLE:
        raise ValueError(f"O título pode ter até {MAX_TITLE} caracteres.")
    return text


def clean_notes(text):
    text = str(text).strip()
    if len(text) > MAX_NOTES:
        raise ValueError(f"As notas podem ter até {MAX_NOTES} caracteres.")
    return text


def clean_priority(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ValueError("Prioridade inválida.") from None
    if not 0 <= value < len(PRIORITIES):
        raise ValueError("Prioridade inválida.")
    return value


def clean_limit(value):
    try:
        value = int(value or 0)
    except (TypeError, ValueError):
        raise ValueError("O limite da coluna precisa ser um número.") from None
    if not 0 <= value <= MAX_WIP:
        raise ValueError(f"O limite da coluna vai de 0 (sem limite) a {MAX_WIP}.")
    return value


def clean_tags(tags):
    """Single-word labels without '#', unique regardless of case; a string is split on commas and spaces."""
    if isinstance(tags, str):
        tags = tags.replace(",", " ").split()
    result, seen = [], set()
    for tag in tags:
        tag = "-".join(str(tag).split()).lstrip("#")
        if not tag:
            continue
        if len(tag) > MAX_TAG:
            raise ValueError(f"Cada etiqueta pode ter até {MAX_TAG} caracteres.")
        if fold(tag) not in seen:
            seen.add(fold(tag))
            result.append(tag)
    if len(result) > MAX_TAGS:
        raise ValueError(f"Use no máximo {MAX_TAGS} etiquetas por cartão.")
    return result


def clean_checklist(items):
    result = []
    for item in items:
        text, done = (item["text"], item.get("done", False)) if isinstance(item, dict) else item
        text = " ".join(str(text).split())
        if not text:
            continue
        if len(text) > MAX_TITLE:
            raise ValueError(f"Cada item da lista pode ter até {MAX_TITLE} caracteres.")
        result.append((text, bool(done)))
    if len(result) > MAX_ITEMS:
        raise ValueError(f"Use no máximo {MAX_ITEMS} itens por lista.")
    return result


def parse_due(text, today=None):
    """Deadline as ISO date. Accepts AAAA-MM-DD, DD/MM, DD/MM/AAAA, hoje, amanhã and weekday names."""
    text = fold(str(text or "").strip())
    if not text:
        return None
    today = today or dt.date.today()
    if text in ("hoje", "amanha"):
        return (today + dt.timedelta(days=text == "amanha")).isoformat()
    weekday = WEEKDAYS.get(text.removesuffix("-feira"))
    if weekday is not None:
        return (today + dt.timedelta(days=(weekday - today.weekday()) % 7)).isoformat()
    try:
        if "/" in text:
            parts = [int(p) for p in text.split("/")]
            if len(parts) == 2:
                parts.append(today.year)
            if len(parts) != 3:
                raise ValueError
            day, month, year = parts
            return dt.date(year + 2000 if year < 100 else year, month, day).isoformat()
        return dt.date.fromisoformat(text).isoformat()
    except ValueError:
        raise ValueError("Data inválida. Use AAAA-MM-DD, DD/MM ou DD/MM/AAAA.") from None


def parse_quick(text, today=None):
    """Quick capture: 'Comprar pão #casa !! @amanhã' → title, tags, priority (! a !!!) and deadline."""
    tags, priority, due, words = [], 0, None, []
    for word in str(text).split():
        if len(word) > 1 and word.startswith("#"):
            tags.append(word)
        elif set(word) == {"!"} and len(word) < len(PRIORITIES):
            priority = len(word)
        elif len(word) > 1 and word.startswith("@"):
            try:
                due = parse_due(word[1:], today)
            except ValueError:
                words.append(word)
        else:
            words.append(word)
    return {"title": clean_title(" ".join(words)), "tags": clean_tags(tags), "priority": priority, "due": due}


def matches(card, query):
    """Every word must match: '#tag' by prefix, '!!' as minimum priority, anything else as text."""
    for token in str(query).split():
        if len(token) > 1 and token.startswith("#"):
            if not any(fold(tag).startswith(fold(token[1:])) for tag in card["tags"]):
                return False
        elif set(token) == {"!"}:
            if card["priority"] < len(token):
                return False
        elif fold(token) not in fold(" ".join((card["title"], card["notes"], *card["tags"]))):
            return False
    return True


def due_label(due, today=None, done=False):
    """Short text and state (overdue, today, soon, later, done) for a card's deadline."""
    if not due:
        return None, None
    today = today or dt.date.today()
    date = dt.date.fromisoformat(due)
    text = date.strftime("%d/%m" if date.year == today.year else "%d/%m/%Y")
    days = (date - today).days
    if done:
        return text, "done"
    if days < 0:
        return f"Atrasado · {text}", "overdue"
    if days == 0:
        return "Hoje", "today"
    if days == 1:
        return "Amanhã", "soon"
    return text, "soon" if days <= 3 else "later"


class KanbanDB:
    def __init__(self, path=None):
        self.db = connect(path)

    def close(self):
        self.db.close()

    def setting(self, key, default=None):
        record = self.db.execute("SELECT value FROM kanban_settings WHERE key=?", (key,)).fetchone()
        return json.loads(record[0]) if record else default

    def set_setting(self, key, value):
        with self.db:
            self.db.execute("INSERT INTO kanban_settings(key,value) VALUES(?,?) "
                            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    # Boards

    def boards(self):
        return self.db.execute("SELECT * FROM kanban_boards ORDER BY position, id").fetchall()

    def board(self, board_id):
        record = self.db.execute("SELECT * FROM kanban_boards WHERE id=?", (board_id,)).fetchone()
        if record is None:
            raise ValueError("Este quadro não existe mais.")
        return record

    def create_board(self, name, columns=DEFAULT_COLUMNS):
        name = clean_name(name, "do quadro")
        columns = [(clean_name(title, "da coluna"), bool(done), 0) for title, done in columns]
        with self.db:
            return self._insert_board(name, columns)[0]

    def ensure_board(self):
        boards = self.boards()
        return boards[0]["id"] if boards else self.create_board(DEFAULT_BOARD)

    def rename_board(self, board_id, name):
        name = clean_name(name, "do quadro")
        self.board(board_id)
        with self.db:
            self.db.execute("UPDATE kanban_boards SET name=? WHERE id=?", (name, board_id))

    def delete_board(self, board_id):
        self.board(board_id)
        columns = "SELECT id FROM kanban_columns WHERE board=?"
        cards = f"SELECT id FROM kanban_cards WHERE column_id IN ({columns})"
        with self.db:
            self.db.execute(f"DELETE FROM kanban_checklist WHERE card IN ({cards})", (board_id,))
            self.db.execute(f"DELETE FROM kanban_cards WHERE column_id IN ({columns})", (board_id,))
            self.db.execute("DELETE FROM kanban_columns WHERE board=?", (board_id,))
            self.db.execute("DELETE FROM kanban_boards WHERE id=?", (board_id,))

    def insert_boards(self, boards):
        """Create boards already validated by exchange.normalize, all in a single transaction."""
        names = {fold(b["name"]) for b in self.boards()}
        created = []
        with self.db:
            for board in boards:
                name = board["name"]
                for n in range(1, 1000):
                    if fold(name) not in names:
                        break
                    suffix = " (importado)" if n == 1 else f" (importado {n})"
                    name = board["name"][:MAX_NAME - len(suffix)].rstrip() + suffix
                names.add(fold(name))
                columns = [(c["name"], c["done"], c["wip_limit"]) for c in board["columns"]]
                board_id, column_ids = self._insert_board(name, columns)
                for column_id, column in zip(column_ids, board["columns"]):
                    counters = [0, 0]
                    for card in column["cards"]:
                        position = counters[card["archived"]]
                        counters[card["archived"]] += 1
                        card_id = self.db.execute(
                            "INSERT INTO kanban_cards(column_id,position,title,notes,priority,due,tags,archived,"
                            "created,updated,done_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (column_id, position, card["title"], card["notes"], card["priority"], card["due"],
                             ",".join(card["tags"]), int(card["archived"]), card["created"], card["updated"],
                             (card["done_at"] or card["updated"]) if column["done"] else None)).lastrowid
                        self._write_checklist(card_id, card["checklist"])
                created.append(board_id)
        return created

    def _insert_board(self, name, columns):
        """Insert a board and its (name, done, wip_limit) columns without committing; callers hold the transaction."""
        position = self.db.execute("SELECT COALESCE(MAX(position)+1, 0) FROM kanban_boards").fetchone()[0]
        board_id = self.db.execute("INSERT INTO kanban_boards(name,position,created) VALUES(?,?,?)",
                                   (name, position, now())).lastrowid
        column_ids = [self.db.execute("INSERT INTO kanban_columns(board,name,position,done,wip_limit) VALUES(?,?,?,?,?)",
                                      (board_id, title, index, int(done), wip_limit)).lastrowid
                      for index, (title, done, wip_limit) in enumerate(columns)]
        return board_id, column_ids

    # Columns

    def columns(self, board_id):
        return self.db.execute(
            "SELECT c.*, (SELECT COUNT(*) FROM kanban_cards k WHERE k.column_id=c.id AND k.archived=0) AS count "
            "FROM kanban_columns c WHERE board=? ORDER BY position, id", (board_id,)).fetchall()

    def column(self, column_id):
        record = self.db.execute("SELECT * FROM kanban_columns WHERE id=?", (column_id,)).fetchone()
        if record is None:
            raise ValueError("Esta coluna não existe mais.")
        return record

    def add_column(self, board_id, name, wip_limit=0, done=False):
        name, wip_limit = clean_name(name, "da coluna"), clean_limit(wip_limit)
        self.board(board_id)
        with self.db:
            position = self.db.execute("SELECT COALESCE(MAX(position)+1, 0) FROM kanban_columns WHERE board=?",
                                       (board_id,)).fetchone()[0]
            return self.db.execute("INSERT INTO kanban_columns(board,name,position,wip_limit,done) VALUES(?,?,?,?,?)",
                                   (board_id, name, position, wip_limit, int(done))).lastrowid

    def update_column(self, column_id, name, wip_limit=0, done=False):
        name, wip_limit = clean_name(name, "da coluna"), clean_limit(wip_limit)
        self.column(column_id)
        with self.db:
            self.db.execute("UPDATE kanban_columns SET name=?, wip_limit=?, done=? WHERE id=?",
                            (name, wip_limit, int(done), column_id))
            self._stamp_done(column_id, done)

    def move_column(self, column_id, offset):
        column = self.column(column_id)
        ids = [c["id"] for c in self.columns(column["board"])]
        index = ids.index(column_id)
        target = min(max(index + offset, 0), len(ids) - 1)
        ids.insert(target, ids.pop(index))
        with self.db:
            self.db.executemany("UPDATE kanban_columns SET position=? WHERE id=?", enumerate_ids(ids))

    def delete_column(self, column_id):
        """Remove a column; its cards (archived too) go to the end of the previous column, or the next one."""
        column = self.column(column_id)
        ids = [c["id"] for c in self.columns(column["board"])]
        if len(ids) == 1:
            raise ValueError("O quadro precisa de pelo menos uma coluna.")
        index = ids.index(column_id)
        target = ids[index - 1] if index else ids[1]
        moving = [r[0] for r in self.db.execute(
            "SELECT id FROM kanban_cards WHERE column_id=? ORDER BY archived, position, id", (column_id,))]
        with self.db:
            start = self._active_count(target)
            self.db.executemany("UPDATE kanban_cards SET column_id=?, position=? WHERE id=?",
                                [(target, start + n, card) for n, card in enumerate(moving)])
            self._stamp_done(target, self.column(target)["done"])
            self.db.execute("DELETE FROM kanban_columns WHERE id=?", (column_id,))
            ids.remove(column_id)
            self.db.executemany("UPDATE kanban_columns SET position=? WHERE id=?", enumerate_ids(ids))
            self._renumber(target)
        return target

    def _stamp_done(self, column_id, done):
        if done:
            self.db.execute("UPDATE kanban_cards SET done_at=COALESCE(done_at, ?) WHERE column_id=?", (now(), column_id))
        else:
            self.db.execute("UPDATE kanban_cards SET done_at=NULL WHERE column_id=?", (column_id,))

    # Cards

    def cards(self, board_id, archived=False):
        return [self._card(r) for r in self.db.execute(
            "SELECT k.*, c.done AS column_done, c.name AS column_name, "
            "(SELECT COUNT(*) FROM kanban_checklist WHERE card=k.id) AS checklist_total, "
            "(SELECT COUNT(*) FROM kanban_checklist WHERE card=k.id AND done=1) AS checklist_done "
            "FROM kanban_cards k JOIN kanban_columns c ON c.id=k.column_id "
            "WHERE c.board=? AND k.archived=? ORDER BY c.position, k.position, k.id", (board_id, int(archived)))]

    def card(self, card_id):
        record = self.db.execute(
            "SELECT k.*, c.done AS column_done, c.name AS column_name, c.board AS board, "
            "(SELECT COUNT(*) FROM kanban_checklist WHERE card=k.id) AS checklist_total, "
            "(SELECT COUNT(*) FROM kanban_checklist WHERE card=k.id AND done=1) AS checklist_done "
            "FROM kanban_cards k JOIN kanban_columns c ON c.id=k.column_id WHERE k.id=?", (card_id,)).fetchone()
        if record is None:
            raise ValueError("Este cartão não existe mais.")
        return self._card(record)

    def add_card(self, column_id, title, notes="", priority=0, due=None, tags=(), checklist=(), top=False):
        values = self._values(title, notes, priority, due, tags)
        checklist = clean_checklist(checklist)
        column = self.column(column_id)
        stamp = now()
        with self.db:
            if top:
                self.db.execute("UPDATE kanban_cards SET position=position+1 WHERE column_id=? AND archived=0",
                                (column_id,))
            position = 0 if top else self._active_count(column_id)
            card_id = self.db.execute(
                "INSERT INTO kanban_cards(column_id,position,title,notes,priority,due,tags,created,updated,done_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (column_id, position, *values, stamp, stamp, stamp if column["done"] else None)).lastrowid
            self._write_checklist(card_id, checklist)
        return card_id

    def update_card(self, card_id, title, notes="", priority=0, due=None, tags=(), checklist=None):
        values = self._values(title, notes, priority, due, tags)
        checklist = None if checklist is None else clean_checklist(checklist)
        self.card(card_id)
        with self.db:
            self.db.execute("UPDATE kanban_cards SET title=?, notes=?, priority=?, due=?, tags=?, updated=? WHERE id=?",
                            (*values, now(), card_id))
            if checklist is not None:
                self.db.execute("DELETE FROM kanban_checklist WHERE card=?", (card_id,))
                self._write_checklist(card_id, checklist)

    def move_card(self, card_id, column_id, index=None, before=None):
        """Place a card in `column_id`, before card `before` or at `index` (end when both are missing).

        Returns True when the card changed place."""
        card, target = self.card(card_id), self.column(column_id)
        if card["archived"]:
            raise ValueError("Restaure o cartão antes de movê-lo.")
        ids = [r[0] for r in self.db.execute(
            "SELECT id FROM kanban_cards WHERE column_id=? AND archived=0 AND id!=? ORDER BY position, id",
            (column_id, card_id))]
        if before is not None and before in ids:
            index = ids.index(before)
        index = len(ids) if index is None else min(max(int(index), 0), len(ids))
        if card["column_id"] == column_id and card["position"] == index:
            return False
        ids.insert(index, card_id)
        stamp = now()
        with self.db:
            if card["column_id"] != column_id:
                done_at = (card["done_at"] or stamp) if target["done"] else None
                self.db.execute("UPDATE kanban_cards SET column_id=?, done_at=?, updated=? WHERE id=?",
                                (column_id, done_at, stamp, card_id))
                self._renumber(card["column_id"])
            self.db.executemany("UPDATE kanban_cards SET position=? WHERE id=?", enumerate_ids(ids))
        return True

    def archive_card(self, card_id, archived=True):
        card = self.card(card_id)
        with self.db:
            position = self._active_count(card["column_id"]) if not archived else card["position"]
            self.db.execute("UPDATE kanban_cards SET archived=?, position=? WHERE id=?",
                            (int(archived), position, card_id))
            self._renumber(card["column_id"])

    def archive_done(self, board_id):
        """Archive every active card in the board's done columns; returns their ids for undo."""
        ids = [r[0] for r in self.db.execute(
            "SELECT k.id FROM kanban_cards k JOIN kanban_columns c ON c.id=k.column_id "
            "WHERE c.board=? AND c.done=1 AND k.archived=0 ORDER BY c.position, k.position", (board_id,))]
        with self.db:
            self.db.executemany("UPDATE kanban_cards SET archived=1 WHERE id=?", ((i,) for i in ids))
        return ids

    def restore_cards(self, ids):
        for card_id in ids:
            self.archive_card(card_id, archived=False)

    def delete_card(self, card_id):
        card = self.card(card_id)
        with self.db:
            self.db.execute("DELETE FROM kanban_checklist WHERE card=?", (card_id,))
            self.db.execute("DELETE FROM kanban_cards WHERE id=?", (card_id,))
            self._renumber(card["column_id"])

    def empty_archive(self, board_id):
        cards = "SELECT k.id FROM kanban_cards k JOIN kanban_columns c ON c.id=k.column_id WHERE c.board=? AND k.archived=1"
        with self.db:
            self.db.execute(f"DELETE FROM kanban_checklist WHERE card IN ({cards})", (board_id,))
            return self.db.execute(f"DELETE FROM kanban_cards WHERE id IN ({cards})", (board_id,)).rowcount

    def checklist(self, card_id):
        return self.db.execute("SELECT * FROM kanban_checklist WHERE card=? ORDER BY position, id", (card_id,)).fetchall()

    def stats(self, board_id, today=None):
        today = (today or dt.date.today()).isoformat()
        cards = self.cards(board_id)
        return {"cards": len(cards),
                "done": sum(bool(c["column_done"]) for c in cards),
                "overdue": sum(bool(c["due"] and c["due"] < today and not c["column_done"]) for c in cards)}

    def _values(self, title, notes, priority, due, tags):
        return (clean_title(title), clean_notes(notes), clean_priority(priority),
                parse_due(due), ",".join(clean_tags(tags)))

    def _write_checklist(self, card_id, items):
        self.db.executemany("INSERT INTO kanban_checklist(card,position,text,done) VALUES(?,?,?,?)",
                            [(card_id, n, text, int(done)) for n, (text, done) in enumerate(items)])

    def _active_count(self, column_id):
        return self.db.execute("SELECT COUNT(*) FROM kanban_cards WHERE column_id=? AND archived=0",
                               (column_id,)).fetchone()[0]

    def _renumber(self, column_id):
        ids = [r[0] for r in self.db.execute(
            "SELECT id FROM kanban_cards WHERE column_id=? AND archived=0 ORDER BY position, id", (column_id,))]
        self.db.executemany("UPDATE kanban_cards SET position=? WHERE id=?", enumerate_ids(ids))

    @staticmethod
    def _card(record):
        card = dict(record)
        card["tags"] = [t for t in card["tags"].split(",") if t]
        return card


def enumerate_ids(ids):
    return [(position, item) for position, item in enumerate(ids)]
