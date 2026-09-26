from gi.repository import Gtk
from .core import Calculator
from .widgets import Page, button, clear, confirm, group, label, row


class CalculatorPage(Page):
    def __init__(self, window):
        super().__init__(window, "Calculadora", "Contas rápidas e funções científicas.")
        try:
            self.calculator = Calculator()
        except OSError:
            self.body.append(label("Compile a biblioteca C com make antes de usar a calculadora.", "error"))
            return
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        panel.add_css_class("calculator-display")
        self.entry = Gtk.Entry(placeholder_text="Ex.: sqrt(144) + 2^3", max_length=4096)
        self.entry.add_css_class("calculator-entry")
        self.entry.connect("activate", lambda _: self.evaluate())
        panel.append(self.entry)
        self.result = Gtk.Label(label="0", xalign=1, selectable=True, wrap=True)
        self.result.add_css_class("calculator-result")
        panel.append(self.result)
        self.body.append(panel)
        grid = Gtk.Grid(column_homogeneous=True, row_homogeneous=True, column_spacing=8, row_spacing=8)
        keys = [
            ["C", "⌫", "(", ")", "÷"],
            ["sqrt", "7", "8", "9", "×"],
            ["sin", "4", "5", "6", "−"],
            ["cos", "1", "2", "3", "+"],
            ["ln", "0", ".", "%", "="],
            ["log", "tan", "π", "e", "^"],
        ]
        for y, line in enumerate(keys):
            for x, key in enumerate(line):
                widget = button(key, lambda k=key: self.key(k), "suggested-action" if key == "=" else None)
                widget.add_css_class("calculator-key")
                grid.attach(widget, x, y, 1, 1)
        self.body.append(grid)
        self.body.append(label("Trigonometria em radianos • % divide por 100 • ^ calcula potências • vírgula ou ponto decimal", "dim-label"))
        self.toolbar.append(button("Copiar resultado", self.copy, icon="edit-copy-symbolic"))
        self.history_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.body.append(self.history_box)
        self.render_history()

    def key(self, key):
        if key == "=":
            self.evaluate()
            return
        if key == "C":
            self.entry.set_text("")
            self.result.set_text("0")
            self.result.remove_css_class("error")
            return
        position = self.entry.get_position()
        bounds = self.entry.get_selection_bounds()
        if bounds:
            self.entry.delete_text(*bounds)
            position = bounds[0]
        if key == "⌫":
            if not bounds and position > 0:
                self.entry.delete_text(position - 1, position)
            return
        text = key + "(" if key in ("sqrt", "sin", "cos", "tan", "ln", "log") else key
        self.entry.insert_text(text, position)
        self.entry.set_position(position + len(text))
        self.entry.grab_focus_without_selecting()

    def evaluate(self):
        expression = self.entry.get_text().strip()
        if not expression:
            return
        try:
            value = self.calculator.evaluate(expression)
            result = format(value, ".15g")
            self.result.set_text(result)
            self.result.remove_css_class("error")
            self.window.store.log("calculator", expression, result)
            self.render_history()
        except ValueError as exc:
            self.result.set_text(str(exc))
            self.result.add_css_class("error")

    def copy(self):
        if hasattr(self, "result"):
            self.get_clipboard().set(self.result.get_text())
            self.notify("Resultado copiado.")

    def render_history(self):
        clear(self.history_box)
        section = group(self.history_box, "Histórico de cálculos")
        section.set_header_suffix(button("Limpar histórico", self.clear_history, icon="edit-clear-all-symbolic"))
        records = self.window.store.history("calculator")
        for record in records[:15]:
            item = row(record["title"], f"= {record['detail']}")
            item.add_suffix(button("Reutilizar", lambda text=record["title"]: self.entry.set_text(text), icon="edit-undo-symbolic"))
            section.add(item)
        if not records:
            section.add(row("Seus cálculos aparecerão aqui."))

    def clear_history(self):
        def remove():
            self.window.store.clear_history("calculator")
            self.render_history()
        confirm(self.window, "Limpar os cálculos?", "Apagar o histórico de cálculos deste computador?", remove, "Limpar", True)
