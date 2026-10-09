"""Dev loader window: pick a fight/event/act and a deck, click Load.

    python -m sts2bot.loader_ui

Needs the game running with a run in progress, this project's patched
STS2MCP build (for deck setting), and the game window visible -- console
commands are sent as real keystrokes (see dev_console.py). Game data comes
from data/gamedata/ (`python -m sts2bot.gamedata` to refresh it).
"""
from __future__ import annotations

import queue
import random
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from sts2bot.client import STS2Client
from sts2bot.deckgen import DeckCard, from_state_deck, stage_deck, starter_deck
from sts2bot.gamedata import GameData
from sts2bot.loader import LoadRequest, default_floor, execute
from sts2bot.tierlist import _character_to_color

KEEP_ACT = "(keep current act)"
NO_ENCHANT = "(none)"
ALL_COLORS = "(all)"


class FilterCombo(ttk.Frame):
    """A combobox with a search box above it: typing narrows the list to
    entries containing every typed word (case-insensitive)."""

    def __init__(self, master, values: list[str], width: int = 60, on_select=None):
        super().__init__(master)
        self.all_values = values
        self.search = tk.StringVar()
        entry = ttk.Entry(self, textvariable=self.search, width=width)
        entry.pack(fill="x")
        self.combo = ttk.Combobox(self, values=values, width=width, state="readonly")
        self.combo.pack(fill="x", pady=(2, 0))
        self.search.trace_add("write", lambda *_: self._filter())
        if on_select:
            self.combo.bind("<<ComboboxSelected>>", lambda _e: on_select())

    def set_values(self, values: list[str]) -> None:
        self.all_values = values
        self._filter()

    def _filter(self) -> None:
        words = self.search.get().lower().split()
        matches = [v for v in self.all_values if all(w in v.lower() for w in words)]
        self.combo["values"] = matches
        if len(matches) == 1:
            self.combo.set(matches[0])
            self.combo.event_generate("<<ComboboxSelected>>")

    def get(self) -> str:
        return self.combo.get()


class LoaderApp:
    def __init__(self, root: tk.Tk, client: STS2Client, gamedata: GameData):
        self.root = root
        self.client = client
        self.gd = gamedata
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.deck: list[DeckCard] = []
        self.encounters = {self._encounter_label(e): e for e in sorted(
            gamedata.encounters, key=lambda e: (e.acts or ("~event",), e.kind, e.id))}

        root.title("STS2 dev loader")
        root.columnconfigure(0, weight=1)
        self._build_target(root).grid(row=0, column=0, sticky="ew", padx=8, pady=4)
        self._build_deck(root).grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
        root.rowconfigure(1, weight=1)
        self._build_actions(root).grid(row=2, column=0, sticky="ew", padx=8, pady=4)
        self.log_box = tk.Text(root, height=8, state="disabled")
        self.log_box.grid(row=3, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self._on_kind_change()
        root.after(100, self._drain_log)

    # ---------------------------------------------------------------- layout
    @staticmethod
    def _encounter_label(e) -> str:
        where = "/".join(e.acts) if e.acts else "event fight"
        enemies = ", ".join(sorted(set(e.lineups[0]))) if e.lineups else ""
        return f"{e.id}   ·   {e.kind} · {where}   ·   {enemies}"

    def _build_target(self, root) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(root, text="What to load")
        self.kind = tk.StringVar(value="fight")
        kinds = ttk.Frame(frame)
        kinds.grid(row=0, column=0, columnspan=2, sticky="w")
        for value, text in (("fight", "Fight"), ("event", "Event"), ("none", "Act / deck only")):
            ttk.Radiobutton(kinds, text=text, value=value, variable=self.kind,
                            command=self._on_kind_change).pack(side="left", padx=(0, 12))

        ttk.Label(frame, text="Fight (type to search):").grid(row=1, column=0, sticky="nw", pady=2)
        self.fight_combo = FilterCombo(frame, list(self.encounters), on_select=self._on_fight_selected)
        self.fight_combo.grid(row=1, column=1, sticky="ew", pady=2)
        ttk.Label(frame, text="Event (type to search):").grid(row=2, column=0, sticky="nw", pady=2)
        self.event_combo = FilterCombo(frame, self.gd.events)
        self.event_combo.grid(row=2, column=1, sticky="ew", pady=2)

        ttk.Label(frame, text="Act:").grid(row=3, column=0, sticky="w", pady=2)
        self.act_combo = ttk.Combobox(frame, values=[KEEP_ACT, *self.gd.acts], state="readonly", width=30)
        self.act_combo.set(KEEP_ACT)
        self.act_combo.grid(row=3, column=1, sticky="w", pady=2)
        frame.columnconfigure(1, weight=1)
        return frame

    def _build_deck(self, root) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(root, text="Deck")
        self.deck_mode = tk.StringVar(value="keep")
        modes = ttk.Frame(frame)
        modes.grid(row=0, column=0, columnspan=3, sticky="w")
        for value, text in (("keep", "Keep current deck"),
                            ("stage", "Stage deck (current Strike/Defend/Bash + floor-1 random cards)"),
                            ("custom", "Custom deck (editor below -- editing it selects this)")):
            ttk.Radiobutton(modes, text=text, value=value, variable=self.deck_mode).pack(anchor="w")

        opts = ttk.Frame(frame)
        opts.grid(row=1, column=0, columnspan=3, sticky="w", pady=4)
        ttk.Label(opts, text="Floor:").pack(side="left")
        self.floor = tk.IntVar(value=2)
        ttk.Spinbox(opts, from_=1, to=60, textvariable=self.floor, width=5).pack(side="left", padx=(2, 12))
        ttk.Label(opts, text="Seed (blank = random):").pack(side="left")
        self.seed = tk.StringVar()
        ttk.Entry(opts, textvariable=self.seed, width=10).pack(side="left", padx=(2, 12))
        ttk.Label(opts, text="Character:").pack(side="left")
        colors = sorted({c.color for c in self.gd.cards if c.rarity == "Basic"})
        self.color = ttk.Combobox(opts, values=colors, state="readonly", width=12)
        self.color.set("ironclad")
        self.color.pack(side="left", padx=2)

        # Editor: current list on the left, add-card controls on the right.
        self.deck_list = tk.Listbox(frame, height=14, selectmode="extended", width=48)
        self.deck_list.grid(row=2, column=0, rowspan=2, sticky="nsew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.deck_list.yview)
        scroll.grid(row=2, column=1, rowspan=2, sticky="ns")
        self.deck_list.configure(yscrollcommand=scroll.set)

        add = ttk.Frame(frame)
        add.grid(row=2, column=2, sticky="new", padx=(8, 0))
        ttk.Label(add, text="Card colour:").grid(row=0, column=0, sticky="w")
        self.card_color = ttk.Combobox(add, values=[ALL_COLORS, *sorted({c.color for c in self.gd.cards})],
                                       state="readonly", width=14)
        self.card_color.set("ironclad")
        self.card_color.grid(row=0, column=1, sticky="w")
        self.card_color.bind("<<ComboboxSelected>>", lambda _e: self._refresh_card_choices())
        ttk.Label(add, text="Card (type to search):").grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.card_combo = FilterCombo(add, [], width=42)
        self.card_combo.grid(row=2, column=0, columnspan=2, sticky="ew")
        ttk.Label(add, text="Upgrades:").grid(row=3, column=0, sticky="w", pady=(4, 0))
        self.add_upgrades = tk.IntVar(value=0)
        ttk.Spinbox(add, from_=0, to=20, textvariable=self.add_upgrades, width=5).grid(row=3, column=1, sticky="w")
        ttk.Label(add, text="Enchantment:").grid(row=4, column=0, sticky="w")
        self.add_enchant = ttk.Combobox(add, values=[NO_ENCHANT, *self.gd.enchantments], state="readonly", width=22)
        self.add_enchant.set(NO_ENCHANT)
        self.add_enchant.grid(row=4, column=1, sticky="w")
        ttk.Label(add, text="Amount:").grid(row=5, column=0, sticky="w")
        self.add_enchant_amount = tk.IntVar(value=1)
        ttk.Spinbox(add, from_=1, to=99, textvariable=self.add_enchant_amount, width=5).grid(row=5, column=1, sticky="w")
        ttk.Button(add, text="Add card", command=self._add_card).grid(row=6, column=0, columnspan=2, sticky="ew", pady=4)

        edit = ttk.Frame(frame)
        edit.grid(row=3, column=2, sticky="sew", padx=(8, 0))
        for i, (text, cmd) in enumerate((
                ("Remove selected", self._remove_selected),
                ("Upgrade selected (+1)", self._upgrade_selected),
                ("Starter deck", self._fill_starter),
                ("Copy deck from game", self._fill_from_game),
                ("Generate stage deck here", self._fill_stage),
                ("Clear", self._clear_deck))):
            ttk.Button(edit, text=text, command=cmd).grid(row=i // 2, column=i % 2, sticky="ew", padx=1, pady=1)

        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        self._refresh_card_choices()
        return frame

    def _build_actions(self, root) -> ttk.Frame:
        frame = ttk.Frame(root)
        self.heal = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="Heal to full first", variable=self.heal).pack(side="left")
        self.load_button = ttk.Button(frame, text="Load", command=self._load)
        self.load_button.pack(side="right")
        return frame

    # ------------------------------------------------------------- handlers
    def _on_kind_change(self) -> None:
        kind = self.kind.get()
        self.fight_combo.combo.configure(state="readonly" if kind == "fight" else "disabled")
        self.event_combo.combo.configure(state="readonly" if kind == "event" else "disabled")

    def _on_fight_selected(self) -> None:
        encounter = self.encounters.get(self.fight_combo.get())
        if encounter:
            self.floor.set(default_floor(encounter))

    def _refresh_card_choices(self) -> None:
        color = self.card_color.get()
        cards = [c for c in self.gd.cards if color == ALL_COLORS or c.color == color]
        self.card_labels = {f"{c.name}  ·  {c.rarity} {c.type}  ·  {c.id}": c.id
                            for c in sorted(cards, key=lambda c: c.name)}
        self.card_combo.set_values(list(self.card_labels))

    def _set_deck(self, deck: list[DeckCard]) -> None:
        self.deck = list(deck)
        self.deck_mode.set("custom")
        self.deck_list.delete(0, "end")
        for card in self.deck:
            self.deck_list.insert("end", card.label(self.gd))

    def _add_card(self) -> None:
        card_id = self.card_labels.get(self.card_combo.get())
        if not card_id:
            return
        enchant = self.add_enchant.get()
        self._set_deck(self.deck + [DeckCard(card_id, self.add_upgrades.get(),
                                             None if enchant == NO_ENCHANT else enchant,
                                             self.add_enchant_amount.get())])

    def _remove_selected(self) -> None:
        selected = set(self.deck_list.curselection())
        self._set_deck([c for i, c in enumerate(self.deck) if i not in selected])

    def _upgrade_selected(self) -> None:
        selected = set(self.deck_list.curselection())
        self._set_deck([DeckCard(c.id, c.upgrades + 1, c.enchantment, c.enchantment_amount) if i in selected else c
                        for i, c in enumerate(self.deck)])
        for i in selected:
            self.deck_list.selection_set(i)

    def _fill_starter(self) -> None:
        try:
            self._set_deck(starter_deck(self.color.get()))
        except ValueError as e:
            messagebox.showerror("Starter deck", str(e))

    def _game_deck(self) -> list[DeckCard] | None:
        try:
            state = self.client.get_state()
        except Exception as e:
            messagebox.showerror("Game not reachable", f"Couldn't read game state: {e}")
            return None
        player = state.get("player") or {}
        color = _character_to_color(player.get("character"))
        if color:
            self.color.set(color)
        return from_state_deck(player.get("deck") or [])

    def _fill_from_game(self) -> None:
        deck = self._game_deck()
        if deck is not None:
            self._set_deck(deck)

    def _rng(self) -> random.Random:
        seed = self.seed.get().strip()
        return random.Random(seed) if seed else random.Random()

    def _fill_stage(self) -> None:
        deck = self._game_deck()
        if deck is not None:
            self._set_deck(stage_deck(deck, self.floor.get(), self.gd, self.color.get(), self._rng()))

    def _clear_deck(self) -> None:
        self._set_deck([])

    def _load(self) -> None:
        kind = self.kind.get()
        target = None
        if kind == "fight":
            encounter = self.encounters.get(self.fight_combo.get())
            if not encounter:
                messagebox.showwarning("Load", "Pick a fight first.")
                return
            target = encounter.id
        elif kind == "event":
            target = self.event_combo.get()
            if not target:
                messagebox.showwarning("Load", "Pick an event first.")
                return

        mode = self.deck_mode.get()
        deck = None
        if mode == "custom":
            if not self.deck and not messagebox.askyesno("Load", "The custom deck is empty. Load with no cards?"):
                return
            deck = self.deck
        elif mode == "stage":
            current = self._game_deck()
            if current is None:
                return
            deck = stage_deck(current, self.floor.get(), self.gd, self.color.get(), self._rng())
            self.log_queue.put("Stage deck: " + ", ".join(c.label(self.gd).split("   ")[0] for c in deck))

        act = self.act_combo.get()
        request = LoadRequest(kind, target, None if act == KEEP_ACT else act, deck, self.heal.get())
        self.load_button.configure(state="disabled")
        threading.Thread(target=self._run_load, args=(request,), daemon=True).start()

    def _run_load(self, request: LoadRequest) -> None:
        try:
            execute(request, self.client, log=self.log_queue.put)
        except Exception as e:
            self.log_queue.put(f"Error: {e}")
        finally:
            self.root.after(0, lambda: self.load_button.configure(state="normal"))

    def _drain_log(self) -> None:
        while not self.log_queue.empty():
            line = self.log_queue.get_nowait()
            self.log_box.configure(state="normal")
            self.log_box.insert("end", line + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.root.after(100, self._drain_log)


def main() -> None:
    try:
        gamedata = GameData()
    except FileNotFoundError as e:
        raise SystemExit(str(e))
    root = tk.Tk()
    LoaderApp(root, STS2Client(), gamedata)
    root.mainloop()


if __name__ == "__main__":
    main()
