import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
import unittest.mock

from ayo_desk.core import Store
from ayo_desk.kanban import exchange, schema
from ayo_desk.kanban.board import KanbanDB, clean_tags, due_label, matches, parse_due, parse_quick

TODAY = dt.date(2026, 9, 26)  # a Saturday


class ParsingTests(unittest.TestCase):
    def test_quick_capture(self):
        self.assertEqual(parse_quick("Comprar pão #casa !! @amanhã", TODAY),
                         {"title": "Comprar pão", "tags": ["casa"], "priority": 2, "due": "2026-09-27"})
        self.assertEqual(parse_quick("Enviar para ana@exemplo.com @nunca", TODAY)["title"],
                         "Enviar para ana@exemplo.com @nunca")
        self.assertEqual(parse_quick("C# e Go !!!! #", TODAY),
                         {"title": "C# e Go !!!! #", "tags": [], "priority": 0, "due": None})
        with self.assertRaises(ValueError):
            parse_quick("#só-etiqueta !!!", TODAY)

    def test_due_dates(self):
        for text, expected in (("hoje", "2026-09-26"), ("Amanhã", "2026-09-27"), ("sexta", "2026-10-02"),
                               ("sábado", "2026-09-26"), ("segunda-feira", "2026-09-28"), ("25/12", "2026-12-25"),
                               ("01/02/27", "2027-02-01"), ("2026-10-05", "2026-10-05"), ("", None), (None, None)):
            with self.subTest(text=text):
                self.assertEqual(parse_due(text, TODAY), expected)
        for text in ("30/02", "32/01/2026", "1/2/3/4", "ontem", "2026-13-01"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_due(text, TODAY)

    def test_due_labels(self):
        self.assertEqual(due_label("2026-09-24", TODAY), ("Atrasado · 24/09", "overdue"))
        self.assertEqual(due_label("2026-09-24", TODAY, done=True), ("24/09", "done"))
        self.assertEqual(due_label("2026-09-26", TODAY), ("Hoje", "today"))
        self.assertEqual(due_label("2026-09-27", TODAY), ("Amanhã", "soon"))
        self.assertEqual(due_label("2027-01-10", TODAY), ("10/01/2027", "later"))
        self.assertEqual(due_label(None, TODAY), (None, None))

    def test_tags_are_single_words_without_duplicates(self):
        self.assertEqual(clean_tags("#Casa, casa  trabalho,,urgente"), ["Casa", "trabalho", "urgente"])
        self.assertEqual(clean_tags(["dia a dia", "#Ação", "acao"]), ["dia-a-dia", "Ação"])
        with self.assertRaises(ValueError):
            clean_tags(["x" * 41])

    def test_search(self):
        card = {"title": "Reunião com o time", "notes": "Levar o relatório", "tags": ["trabalho"], "priority": 2}
        for query, expected in (("reuniao", True), ("RELATÓRIO", True), ("#trab", True), ("#casa", False),
                                ("!!", True), ("!!!", False), ("reunião #trabalho !", True), ("time casa", False)):
            with self.subTest(query=query):
                self.assertEqual(matches(card, query), expected)


class BoardCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "kanban.sqlite3"
        self.kanban = KanbanDB(self.path)
        self.board = self.kanban.ensure_board()
        self.todo, self.doing, self.done = (c["id"] for c in self.kanban.columns(self.board))

    def tearDown(self):
        self.kanban.close()
        self.temp.cleanup()

    def titles(self, column):
        return [c["title"] for c in self.kanban.cards(self.board) if c["column_id"] == column]

    def add(self, column, *titles):
        return [self.kanban.add_card(column, title) for title in titles]


class BoardTests(BoardCase):
    def test_first_board_and_persistence(self):
        self.assertEqual(self.kanban.ensure_board(), self.board)
        self.assertEqual([c["name"] for c in self.kanban.columns(self.board)], ["A fazer", "Fazendo", "Feito"])
        card = self.kanban.add_card(self.todo, "  Escrever   testes ", "notas", 3, "2026-10-01", "#ayo", [("um", True)])
        self.kanban.close()
        self.kanban = KanbanDB(self.path)
        saved = self.kanban.card(card)
        self.assertEqual((saved["title"], saved["priority"], saved["due"], saved["tags"]),
                         ("Escrever testes", 3, "2026-10-01", ["ayo"]))
        self.assertEqual([(i["text"], i["done"]) for i in self.kanban.checklist(card)], [("um", 1)])
        self.assertEqual((saved["checklist_done"], saved["checklist_total"]), (1, 1))
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_invalid_input_changes_nothing(self):
        for kwargs in ({"title": " "}, {"title": "x", "priority": 4}, {"title": "x", "due": "31/02"},
                       {"title": "x" * 301}, {"title": "x", "checklist": [("y" * 301, False)]}):
            with self.subTest(kwargs=str(kwargs)[:40]), self.assertRaises(ValueError):
                self.kanban.add_card(self.todo, **kwargs)
        self.assertEqual(self.kanban.cards(self.board), [])
        with self.assertRaises(ValueError):
            self.kanban.add_card(9999, "Coluna que não existe")
        with self.assertRaises(ValueError):
            self.kanban.create_board("   ")

    def test_order_top_and_moves_within_a_column(self):
        a, b, c = self.add(self.todo, "A", "B", "C")
        self.kanban.add_card(self.todo, "Topo", top=True)
        self.assertEqual(self.titles(self.todo), ["Topo", "A", "B", "C"])
        self.assertTrue(self.kanban.move_card(c, self.todo, index=0))
        self.assertEqual(self.titles(self.todo), ["C", "Topo", "A", "B"])
        self.assertFalse(self.kanban.move_card(c, self.todo, index=0), "Soltar no mesmo lugar não muda nada")
        self.kanban.move_card(c, self.todo, before=b)
        self.assertEqual(self.titles(self.todo), ["Topo", "A", "C", "B"])
        self.kanban.move_card(a, self.todo)
        self.assertEqual(self.titles(self.todo), ["Topo", "C", "B", "A"])
        self.kanban.move_card(a, self.todo, index=-5)
        self.assertEqual(self.titles(self.todo), ["A", "Topo", "C", "B"])
        positions = [c["position"] for c in self.kanban.cards(self.board)]
        self.assertEqual(positions, [0, 1, 2, 3])

    def test_moving_between_columns_tracks_completion(self):
        a, b = self.add(self.todo, "A", "B")
        (x,) = self.add(self.doing, "X")
        self.kanban.move_card(a, self.doing, before=x)
        self.assertEqual(self.titles(self.todo), ["B"])
        self.assertEqual(self.titles(self.doing), ["A", "X"])
        self.assertEqual(self.kanban.card(self.kanban.cards(self.board)[0]["id"])["position"], 0)
        self.kanban.move_card(a, self.done)
        finished = self.kanban.card(a)["done_at"]
        self.assertTrue(finished)
        self.assertEqual(self.kanban.stats(self.board), {"cards": 3, "done": 1, "overdue": 0})
        self.kanban.move_card(a, self.todo)
        self.assertIsNone(self.kanban.card(a)["done_at"])
        other = self.kanban.create_board("Outro")
        target = self.kanban.columns(other)[0]["id"]
        self.kanban.move_card(b, target)
        self.assertEqual([c["title"] for c in self.kanban.cards(other)], ["B"])

    def test_overdue_ignores_done_columns(self):
        self.kanban.add_card(self.todo, "Atrasado", due="2026-09-20")
        self.kanban.add_card(self.done, "Feito atrasado", due="2026-09-20")
        self.kanban.add_card(self.todo, "No prazo", due="2026-09-30")
        self.assertEqual(self.kanban.stats(self.board, TODAY), {"cards": 3, "done": 1, "overdue": 1})

    def test_archive_restore_and_archive_done(self):
        a, b, c = self.add(self.todo, "A", "B", "C")
        self.kanban.archive_card(b)
        self.assertEqual(self.titles(self.todo), ["A", "C"])
        self.assertEqual([c["position"] for c in self.kanban.cards(self.board)], [0, 1])
        with self.assertRaises(ValueError):
            self.kanban.move_card(b, self.doing)
        self.kanban.restore_cards([b])
        self.assertEqual(self.titles(self.todo), ["A", "C", "B"], "Restaurado volta para o fim da coluna")
        self.kanban.move_card(a, self.done)
        self.kanban.move_card(c, self.done)
        ids = self.kanban.archive_done(self.board)
        self.assertEqual(sorted(ids), sorted([a, c]))
        self.assertEqual(self.titles(self.done), [])
        self.assertEqual(len(self.kanban.cards(self.board, archived=True)), 2)
        self.kanban.restore_cards(ids)
        self.assertEqual(sorted(self.titles(self.done)), ["A", "C"])
        self.kanban.archive_card(a)
        self.assertEqual(self.kanban.empty_archive(self.board), 1)
        with self.assertRaises(ValueError):
            self.kanban.card(a)

    def test_columns_order_limit_and_delete_keeps_cards(self):
        review = self.kanban.add_column(self.board, "Revisão", 2)
        self.kanban.move_column(review, -2)
        self.assertEqual([c["name"] for c in self.kanban.columns(self.board)], ["A fazer", "Revisão", "Fazendo", "Feito"])
        self.kanban.move_column(review, -10)
        self.assertEqual(self.kanban.columns(self.board)[0]["id"], review)
        with self.assertRaises(ValueError):
            self.kanban.update_column(review, "Revisão", -1)
        self.add(self.doing, "D1")
        moving = self.add(self.done, "F1", "F2")
        self.kanban.archive_card(moving[1])
        self.assertEqual(self.kanban.delete_column(self.done), self.doing)
        self.assertEqual(self.titles(self.doing), ["D1", "F1"])
        self.assertIsNone(self.kanban.card(moving[0])["done_at"], "Saiu da coluna de concluídos")
        self.assertEqual(self.kanban.card(moving[1])["column_id"], self.doing, "Arquivados também são levados")
        self.kanban.delete_column(review)
        self.assertEqual(self.kanban.columns(self.board)[0]["id"], self.todo)
        self.kanban.delete_column(self.doing)
        self.assertEqual(self.titles(self.todo), ["D1", "F1"])
        with self.assertRaises(ValueError):
            self.kanban.delete_column(self.todo)

    def test_marking_a_column_done_stamps_its_cards(self):
        (card,) = self.add(self.doing, "A")
        self.kanban.update_column(self.doing, "Pronto", 0, True)
        self.assertTrue(self.kanban.card(card)["done_at"])
        self.kanban.update_column(self.doing, "Fazendo", 0, False)
        self.assertIsNone(self.kanban.card(card)["done_at"])

    def test_update_card_and_checklist(self):
        card = self.kanban.add_card(self.todo, "A", checklist=[("um", False), ("dois", True)])
        self.kanban.update_card(card, "A2", "notas", 1, "hoje", ["x"])
        self.assertEqual(len(self.kanban.checklist(card)), 2, "Sem checklist, a lista fica como estava")
        self.kanban.update_card(card, "A2", "", 0, None, [], [("três", True), ("", False)])
        self.assertEqual([i["text"] for i in self.kanban.checklist(card)], ["três"])
        self.assertEqual(self.kanban.card(card)["due"], None)

    def test_delete_board_removes_everything(self):
        card = self.kanban.add_card(self.todo, "A", checklist=[("item", False)])
        keep = self.kanban.create_board("Fica")
        self.kanban.add_card(self.kanban.columns(keep)[0]["id"], "B", checklist=[("item", False)])
        self.kanban.delete_board(self.board)
        self.assertEqual([b["name"] for b in self.kanban.boards()], ["Fica"])
        with self.assertRaises(ValueError):
            self.kanban.card(card)
        for table, expected in (("kanban_columns", 3), ("kanban_cards", 1), ("kanban_checklist", 1)):
            self.assertEqual(self.kanban.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], expected)


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_own_file_under_xdg_data_home_and_desk_database_untouched(self):
        with unittest.mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.data)}):
            desk = Store()
            kanban = KanbanDB()
            try:
                kanban.set_setting("compact", True)
                kanban.add_card(kanban.columns(kanban.ensure_board())[0]["id"], "Primeiro cartão")
                tables = {r[0] for r in desk.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertFalse({t for t in tables if t.startswith("kanban")}, "O Kanban não mexe no desk.sqlite3")
            finally:
                kanban.close()
                desk.close()
            path = self.data / "ayo-desk/kanban.sqlite3"
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            kanban = KanbanDB()
            try:
                self.assertIs(kanban.setting("compact"), True)
                self.assertEqual(kanban.setting("board", 7), 7)
                self.assertEqual(kanban.db.execute("PRAGMA user_version").fetchone()[0], len(schema.MIGRATIONS))
            finally:
                kanban.close()

    def test_failed_migration_rolls_back_and_newer_file_is_refused(self):
        path = self.data / "kanban.sqlite3"
        KanbanDB(path).close()
        broken = (*schema.MIGRATIONS, "CREATE TABLE half_done(x); SELECT * FROM missing_table;")
        with unittest.mock.patch.object(schema, "MIGRATIONS", broken), self.assertRaises(sqlite3.OperationalError):
            KanbanDB(path)
        db = sqlite3.connect(path)
        try:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], len(schema.MIGRATIONS))
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='half_done'").fetchone())
            db.execute(f"PRAGMA user_version = {len(schema.MIGRATIONS) + 1}")
        finally:
            db.close()
        with self.assertRaises(RuntimeError):
            KanbanDB(path)


class ExchangeTests(BoardCase):
    def test_json_round_trip(self):
        self.kanban.update_column(self.doing, "Fazendo", 3)
        a = self.kanban.add_card(self.todo, "Com \"aspas\" e acentuação", "linha 1\nlinha 2", 2, "2026-10-01",
                                 ["casa"], [("item", True)])
        self.kanban.add_card(self.done, "Terminado")
        self.kanban.archive_card(self.kanban.add_card(self.doing, "Guardado"))
        text = exchange.dumps(exchange.export_boards(self.kanban, [self.board]))
        self.assertEqual(json.loads(text)["format"], "ayo-kanban")
        (copy,) = self.kanban.insert_boards(exchange.loads(text))
        self.assertEqual(self.kanban.board(copy)["name"], "Meu quadro (importado)")
        self.assertEqual([(c["name"], c["wip_limit"], c["done"]) for c in self.kanban.columns(copy)],
                         [("A fazer", 0, 0), ("Fazendo", 3, 0), ("Feito", 0, 1)])
        original, imported = self.kanban.card(a), self.kanban.cards(copy)[0]
        for key in ("title", "notes", "priority", "due", "tags", "created", "position"):
            self.assertEqual(original[key], imported[key], key)
        self.assertEqual([(i["text"], i["done"]) for i in self.kanban.checklist(imported["id"])], [("item", 1)])
        self.assertEqual([c["title"] for c in self.kanban.cards(copy, archived=True)], ["Guardado"])
        self.assertTrue(self.kanban.cards(copy)[-1]["done_at"])
        self.kanban.insert_boards(exchange.loads(text))
        self.assertIn("Meu quadro (importado 2)", [b["name"] for b in self.kanban.boards()])

    def test_invalid_files_are_rejected_before_writing(self):
        good = exchange.export_boards(self.kanban, [self.board])
        broken = json.loads(json.dumps(good))
        broken["boards"].append({"name": "Ruim", "columns": [{"name": "C", "cards": [{"title": ""}]}]})
        for text in ("{", "[]", json.dumps({"format": "outro"}), json.dumps({**good, "version": 99}),
                     json.dumps({**good, "boards": []}), json.dumps(broken),
                     json.dumps({**good, "boards": [{"name": "X", "columns": "não"}]})):
            with self.subTest(text=text[:40]), self.assertRaises(ValueError):
                exchange.loads(text)
        self.assertEqual(len(self.kanban.boards()), 1)

    def test_files_and_minimal_board(self):
        path = Path(self.temp.name) / "quadro.json"
        exchange.save(path, json.dumps({"format": "ayo-kanban", "version": 1,
                                        "boards": [{"name": "Mínimo", "columns": []}]}))
        self.assertFalse(Path(f"{path}.part").exists())
        (board,) = self.kanban.insert_boards(exchange.read(path))
        self.assertEqual(len(self.kanban.columns(board)), 3, "Quadro sem colunas recebe as colunas padrão")
        path.write_bytes(b"\xff\xfe")
        with self.assertRaises(ValueError):
            exchange.read(path)

    def test_markdown(self):
        card = self.kanban.add_card(self.todo, "Estudar *GTK*", "Ler a doc\nFazer exemplo", 3, "2026-09-20", ["gtk"],
                                    [("DropTarget", True)])
        self.kanban.add_card(self.done, "Pronto")
        text = exchange.to_markdown(self.kanban, self.board, TODAY)
        self.assertIn("# Meu quadro", text)
        self.assertIn("- [ ] **Estudar \\*GTK\\*** · prioridade alta · prazo 20/09/2026 (atrasado) · #gtk", text)
        self.assertIn("  Ler a doc\n  Fazer exemplo\n  - [x] DropTarget", text)
        self.assertIn("## Fazendo (0)\n\n_Sem cartões._", text)
        self.assertIn("- [x] **Pronto**", text)
        self.assertTrue(self.kanban.card(card))


if __name__ == "__main__":
    unittest.main()
