"""The offline scenario editor: author intent stays editable and reviewable."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import uuid


class ScenarioEditor:
    def __init__(self, app):
        from autoquestgen import PALETTE
        self.app = app
        self.scan = app.scan_result
        self.selection = list(app.selected_ids)
        self.plan = deepcopy(getattr(app, "_scenario_draft", None) or getattr(app, "_scenario_plan", None) or {})
        self.load_warning = ""
        raw_rows = self.plan.get("stages", []) if isinstance(self.plan, dict) else None
        sane = (isinstance(raw_rows, list) and len(raw_rows) <= 64
                and isinstance(self.plan.get("allow_vanilla", True), bool)
                and isinstance(self.plan.get("notes", []), list)
                and all(isinstance(note, str) for note in self.plan.get("notes", [])))
        seen = set()
        for row in raw_rows if sane else []:
            if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]+", row["id"]) or row["id"] in seen
                    or any(not isinstance(row.get(key, ""), str) for key in ("title", "goal", "mod", "description"))
                    or any(not isinstance(row.get(key, []), list) or any(not isinstance(v, str) for v in row.get(key, []))
                           for key in ("requirements", "depends_on"))):
                sane = False
                break
            seen.add(row["id"])
        if not sane:
            app.cfg["scenario_recovery"] = deepcopy(self.plan)
            self.plan = {}
            self.load_warning = "The saved draft has invalid stage data. Its original content is retained in settings under scenario_recovery; start a new draft below."
        for key in ("title", "premise", "mode", "preferred_start"):
            if key in self.plan and not isinstance(self.plan[key], str):
                self.plan.pop(key)
        self.stages = self.plan.setdefault("stages", [])
        self.current = None
        self.top = top = tk.Toplevel(app.root)
        top.title("Campaign planner")
        top.geometry("1080x850")
        top.minsize(980, 760)
        top.transient(app.root)
        top.configure(bg=PALETTE["bg"])
        frame = ttk.Frame(top, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Write the world’s next chapter", style="Head.TLabel").pack(anchor="w")
        ttk.Label(frame, text="Shape a story from installed goals. Arrange the stages, then review the actual prerequisites.",
                  style="Muted.TLabel").pack(anchor="w", pady=(6, 12))
        footer = ttk.Frame(frame)
        footer.pack(side="bottom", fill="x", pady=(10, 0))
        self.feedback = ttk.Label(footer, wraplength=560, justify="left")
        self.feedback.pack(side="left", fill="x", expand=True)
        ttk.Button(footer, text="Use this campaign", style="Accent.TButton", command=self.use).pack(side="right")
        ttk.Button(footer, text="Save draft", command=self.save_draft).pack(side="right", padx=8)
        self.tabs = tabs = ttk.Notebook(frame)
        tabs.pack(fill="both", expand=True)
        story = ttk.Frame(tabs, padding=12)
        review = ttk.Frame(tabs, padding=12)
        tabs.add(story, text="Story & stages")
        tabs.add(review, text="Review & starting resources")
        files = ttk.Frame(review)
        files.pack(side="bottom", fill="x", pady=(8, 0))
        ttk.Button(files, text="Export scenario…", command=self.export).pack(side="left")
        ttk.Button(files, text="Open scenario…", command=self.open).pack(side="left", padx=8)
        ttk.Label(files, text="Local JSON files keep stages editable and shareable.", style="Muted.TLabel").pack(side="left", padx=8)
        self.review_text = tk.Text(review, wrap="word", bg=PALETTE["bg"], fg=PALETTE["text"],
                                   relief="flat", padx=16, pady=16, font=("Segoe UI", 10))
        review_scroll = ttk.Scrollbar(review, orient="vertical", command=self.review_text.yview)
        review_scroll.pack(side="right", fill="y")
        self.review_text.configure(yscrollcommand=review_scroll.set)
        self.review_text.pack(fill="both", expand=True)
        self.title = tk.StringVar(value=self.plan.get("title", "A World Worth Growing"))
        ttk.Label(story, text="Campaign title").pack(anchor="w")
        ttk.Entry(story, textvariable=self.title).pack(fill="x", pady=(4, 8))
        ttk.Label(story, text="Your world and its goal").pack(anchor="w")
        self.premise = tk.Text(story, height=2, wrap="word", bg=PALETTE["surface2"], fg=PALETTE["text"],
                               insertbackground=PALETTE["text"], relief="flat", padx=8, pady=6, font=("Segoe UI", 10))
        self.premise.pack(fill="x", pady=(4, 8))
        self.premise.insert("1.0", self.plan.get("premise", "Start with Botania in a barren Overworld. Restore water, soil, grass and trees."))
        options = ttk.Frame(story)
        options.pack(fill="x", pady=(0, 8))
        self.mode = tk.StringVar(value=self.plan.get("mode", "assisted"))
        ttk.Radiobutton(options, text="Suggest a route", value="assisted", variable=self.mode).pack(side="left")
        ttk.Radiobutton(options, text="Choose my own stages", value="authored", variable=self.mode).pack(side="left", padx=10)
        self.mod_labels = {m["mod_id"]: m.get("name") or m["mod_id"] for m in self.scan.get("mods", [])
                           if not self.selection or m["mod_id"] in self.selection}
        self.mod_labels["minecraft"] = "Minecraft"
        # Include the ID so two display names can never select the wrong mod.
        self.mod_choices = {f"{name} ({mid})": mid for mid, name in sorted(self.mod_labels.items(), key=lambda x: x[1].lower())}
        start_id = self.plan.get("preferred_start", "botania")
        self.start = tk.StringVar(value=next((label for label, mid in self.mod_choices.items() if mid == start_id), ""))
        ttk.Label(options, text="Start with").pack(side="left", padx=(10, 6))
        ttk.Combobox(options, textvariable=self.start, values=list(self.mod_choices), state="readonly", width=28).pack(side="left")
        ttk.Button(options, text="Suggest stages", command=self.suggest).pack(side="right")
        self.vanilla = tk.BooleanVar(value=self.plan.get("allow_vanilla", True))
        ttk.Checkbutton(story, text="Allow vanilla resources and tree growth as campaign stages", variable=self.vanilla).pack(anchor="w")
        ttk.Label(story, text="Suggestions use supported mechanics from the mods selected in Step 1. You can edit every stage.",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 10))
        columns = ttk.Panedwindow(story, orient="horizontal")
        columns.pack(fill="both", expand=True)
        left, right = ttk.Frame(columns), ttk.Frame(columns, padding=(14, 0, 0, 0))
        columns.add(left, weight=4)
        columns.add(right, weight=6)
        ttk.Label(left, text="The journey", style="Head.TLabel").pack(anchor="w", pady=(0, 6))
        actions = ttk.Frame(left)
        actions.pack(side="bottom", fill="x", pady=(8, 0))
        for label, command in (("Add", self.add), ("Remove", self.remove),
                               ("↑", lambda: self.move(-1)), ("↓", lambda: self.move(1))):
            ttk.Button(actions, text=label, command=command, width=7).pack(side="left", padx=(0, 4))
        ttk.Button(left, text="Undo last suggestion / removal", command=self.undo).pack(side="bottom", anchor="w", pady=(8, 0))
        ttk.Button(left, text="Link stages in this order", command=self.chain).pack(side="bottom", anchor="w", pady=(8, 0))
        tree_frame = ttk.Frame(left)
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tree_frame, columns=("title", "completion"), show="headings", selectmode="browse", height=7)
        self.tree.heading("title", text="Stage")
        self.tree.heading("completion", text="Completion")
        self.tree.column("title", width=240, minwidth=150)
        self.tree.column("completion", width=95, stretch=False)
        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        tree_scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(fill="both", expand=True)
        detail_tabs = ttk.Notebook(right)
        detail_tabs.pack(fill="both", expand=True)
        right = ttk.Frame(detail_tabs, padding=8)
        advanced = ttk.Frame(detail_tabs, padding=8)
        detail_tabs.add(right, text="Player experience")
        detail_tabs.add(advanced, text="Methods & prerequisites")
        self.stage_title, self.goal, self.mod = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.completion = tk.StringVar(value="Confirm the action")
        self.requirements = tk.StringVar()
        ttk.Label(right, text="Stage title").pack(anchor="w")
        ttk.Entry(right, textvariable=self.stage_title).pack(fill="x", pady=(3, 7))
        ttk.Label(right, text="Goal or tool needed").pack(anchor="w")
        goal_row = ttk.Frame(right)
        goal_row.pack(fill="x", pady=(3, 7))
        ttk.Entry(goal_row, textvariable=self.goal).pack(side="left", fill="x", expand=True)
        ttk.Button(goal_row, text="Explore…", command=lambda: app._show_goal_browser(on_choose=self.goal.set)).pack(side="right", padx=(6, 0))
        pair = ttk.Frame(right)
        pair.pack(fill="x", pady=(0, 7))
        ttk.Combobox(pair, textvariable=self.mod, values=["Any selected mod"] + list(self.mod_choices), state="readonly", width=30).pack(side="left", fill="x", expand=True)
        ttk.Combobox(pair, textvariable=self.completion, values=["Detect the objective", "Confirm the action"], state="readonly", width=22).pack(side="right", padx=(8, 0))
        ttk.Label(right, text="What the player does / completion condition").pack(anchor="w")
        self.description = tk.Text(right, height=4, wrap="word", bg=PALETTE["surface2"], fg=PALETTE["text"],
                                   insertbackground=PALETTE["text"], relief="flat", padx=8, pady=6, font=("Segoe UI", 10))
        ttk.Button(right, text="Check this campaign", command=self.check).pack(side="bottom", anchor="e", pady=(6, 0))
        self.description.pack(fill="both", expand=True, pady=(3, 7))
        from quest_scenarios import available_capabilities
        self.methods = {f"{c['title']} · {self.mod_labels.get(c['mod'], c['mod'])}": c
                        for c in available_capabilities(self.scan, self.selection, allow_vanilla=self.vanilla.get())}
        self.method = tk.StringVar()
        ttk.Label(advanced, text="Replace this stage with a verified method").pack(anchor="w")
        self.method_combo = ttk.Combobox(advanced, textvariable=self.method, values=list(self.methods), state="readonly")
        self.method_combo.pack(fill="x", pady=(3, 7))
        ttk.Button(advanced, text="Use this method", command=self.apply_method).pack(anchor="e", pady=(0, 10))
        ttk.Label(advanced, text="Supporting goals (separate with semicolons)").pack(anchor="w")
        ttk.Entry(advanced, textvariable=self.requirements).pack(fill="x", pady=(3, 7))
        ttk.Label(advanced, text="Unlock after these stages (click to select several)").pack(anchor="w")
        self.dependencies = tk.Listbox(advanced, height=3, selectmode="multiple", exportselection=False,
                                       bg=PALETTE["surface2"], fg=PALETTE["text"], relief="flat", font=("Segoe UI", 10))
        self.dependencies.pack(fill="both", expand=True, pady=(3, 7))
        ttk.Button(advanced, text="Check this campaign", command=self.check).pack(anchor="e")
        self.vanilla.trace_add("write", self.refresh_methods)
        self.tree.bind("<<TreeviewSelect>>", self.select)
        tabs.bind("<<NotebookTabChanged>>", lambda _e: self.check(show_review=False) if tabs.index(tabs.select()) == 1 else None)
        top.bind("<Escape>", lambda _e: top.destroy())
        self.refresh()
        self.feedback.configure(text=self.load_warning or "Draft locally. Nothing changes in Minecraft until you build and install a quest book.")

    def save_stage(self):
        row = next((s for s in self.stages if s["id"] == self.current), None)
        if row is None:
            return
        new_goal = self.goal.get().strip()
        if new_goal != row.get("goal", ""):
            # An authored replacement must not inherit a different mechanic's evidence.
            for key in ("capability_id", "source", "mechanic_note"):
                row.pop(key, None)
        row.update(title=self.stage_title.get().strip(), goal=new_goal,
                   mod=self.mod_choices.get(self.mod.get(), ""),
                   description=self.description.get("1.0", "end").strip(),
                   completion="manual" if self.completion.get() == "Confirm the action" else "objective",
                   requirements=[s.strip() for s in self.requirements.get().split(";") if s.strip()],
                   depends_on=[self.dependency_ids[i] for i in self.dependencies.curselection()])
        if self.tree.exists(row["id"]):
            self.tree.item(row["id"], values=(row["title"] or "Untitled stage", "Confirm" if row["completion"] == "manual" else "Detected"))

    def refresh_methods(self, *_args):
        from quest_scenarios import available_capabilities
        self.methods = {f"{c['title']} · {self.mod_labels.get(c['mod'], c['mod'])}": c
                        for c in available_capabilities(self.scan, self.selection, allow_vanilla=self.vanilla.get())}
        self.method_combo.configure(values=list(self.methods))
        if self.method.get() not in self.methods:
            self.method.set("")

    def apply_method(self):
        method = self.methods.get(self.method.get())
        if not method or not self.current:
            return
        self.previous = self.collect()
        row = next(s for s in self.stages if s["id"] == self.current)
        selected = row["id"]
        capability = deepcopy(method)
        capability["capability_id"] = capability.pop("id")
        row.update(capability)
        self.refresh(selected)

    def select(self, _event=None):
        selected = self.tree.selection()
        if not selected or selected[0] == self.current:
            return
        self.save_stage()
        self.current = selected[0]
        row = next(s for s in self.stages if s["id"] == self.current)
        self.stage_title.set(row.get("title", ""))
        self.goal.set(row.get("goal", ""))
        self.mod.set(next((label for label, mid in self.mod_choices.items() if mid == row.get("mod")), "Any selected mod"))
        self.completion.set("Confirm the action" if row.get("completion") == "manual" else "Detect the objective")
        self.description.delete("1.0", "end")
        self.description.insert("1.0", row.get("description", ""))
        self.requirements.set("; ".join(row.get("requirements", [])))
        self.dependencies.delete(0, "end")
        self.dependency_ids = []
        later = False
        for stage in self.stages:
            if stage["id"] == row["id"]:
                later = True
                continue
            self.dependency_ids.append(stage["id"])
            self.dependencies.insert("end", (stage.get("title") or "Untitled stage") + (" (move earlier to use)" if later else ""))
            if stage["id"] in row.get("depends_on", []):
                self.dependencies.selection_set(len(self.dependency_ids) - 1)

    def refresh(self, selected=None):
        self.current = None
        self.tree.delete(*self.tree.get_children())
        for i, row in enumerate(self.stages, 1):
            self.tree.insert("", "end", iid=row["id"], values=(row.get("title") or f"Stage {i}",
                             "Confirm" if row.get("completion") == "manual" else "Detected"))
        if self.stages:
            key = selected if selected and self.tree.exists(selected) else self.stages[0]["id"]
            self.tree.selection_set(key)
            self.select()
        else:
            self.stage_title.set("")
            self.goal.set("")
            self.description.delete("1.0", "end")
            self.requirements.set("")
            self.dependencies.delete(0, "end")

    def collect(self):
        self.save_stage()
        return {**deepcopy(self.plan), "title": self.title.get().strip() or "Your Campaign",
                "premise": self.premise.get("1.0", "end").strip(), "stages": deepcopy(self.stages),
                "mode": self.mode.get(), "preferred_start": self.mod_choices.get(self.start.get(), ""),
                "allow_vanilla": self.vanilla.get()}

    def suggest(self):
        from quest_scenarios import propose_scenario
        draft = self.collect()
        if self.mode.get() == "authored":
            if not self.stages:
                self.add()
            self.feedback.configure(text="Choose each stage’s goal, mod and completion condition, then arrange the journey.")
            return
        if self.stages:
            # Keep the existing draft available for recovery; no destructive confirmation flow.
            self.previous = draft
        self.plan = propose_scenario(self.scan, self.selection, draft["premise"],
                                     preferred_start=draft["preferred_start"], allow_vanilla=draft["allow_vanilla"])
        self.stages = self.plan["stages"]
        if not draft["title"] or draft["title"] in ("Your Campaign", "A World Worth Growing"):
            self.title.set(self.plan["title"])
        self.refresh()
        self.check(show_review=False)

    def add(self):
        self.save_stage()
        row = {"id": "stage_" + uuid.uuid4().hex[:10], "title": "New chapter", "goal": "", "mod": "",
               "description": "", "completion": "objective", "requirements": [],
               "depends_on": [self.stages[-1]["id"]] if self.stages else []}
        self.stages.append(row)
        self.refresh(row["id"])

    def remove(self):
        selected = self.current
        if not selected:
            return
        self.save_stage()
        row = next(s for s in self.stages if s["id"] == selected)
        self.previous = self.collect()
        self.stages.remove(row)
        for stage in self.stages:
            if selected in stage.get("depends_on", []):
                stage["depends_on"] = list(dict.fromkeys([d for d in stage["depends_on"] if d != selected] + row.get("depends_on", [])))
        self.refresh()

    def move(self, direction):
        if not self.current:
            return
        self.save_stage()
        index = next(i for i, s in enumerate(self.stages) if s["id"] == self.current)
        other = index + direction
        if not 0 <= other < len(self.stages):
            return
        selected = self.current
        chained = all(s.get("depends_on", []) == ([self.stages[i - 1]["id"]] if i else []) for i, s in enumerate(self.stages))
        self.stages[index], self.stages[other] = self.stages[other], self.stages[index]
        if chained:
            for i, stage in enumerate(self.stages):
                stage["depends_on"] = [self.stages[i - 1]["id"]] if i else []
        else:
            self.feedback.configure(text="Order changed. Check custom unlock links before using this campaign.")
        self.refresh(selected)

    def chain(self):
        self.save_stage()
        selected = self.current
        for i, stage in enumerate(self.stages):
            stage["depends_on"] = [self.stages[i - 1]["id"]] if i else []
        self.refresh(selected)
        self.feedback.configure(text="Each stage now unlocks after the previous stage completes.")

    def save_draft(self):
        self.app._scenario_draft = self.collect()
        self.app._save_cfg()
        self.feedback.configure(text="Draft saved on this computer. Reopen the planner to continue editing it.")

    def export(self):
        path = filedialog.asksaveasfilename(parent=self.top, title="Export scenario", defaultextension=".json",
                                          filetypes=[("Scenario file", "*.json")], initialfile="my-campaign.json")
        if not path:
            return
        try:
            Path(path).write_text(json.dumps(self.collect(), indent=2, ensure_ascii=False), encoding="utf-8")
            self.feedback.configure(text="Scenario saved. It will be checked against the selected pack when opened and built.")
        except OSError as exc:
            messagebox.showerror("Could not export scenario", str(exc), parent=self.top)

    def open(self):
        path = filedialog.askopenfilename(parent=self.top, title="Open scenario", filetypes=[("Scenario file", "*.json")])
        if not path:
            return
        try:
            if Path(path).stat().st_size > 2_000_000:
                raise ValueError("This scenario file is too large. Use a file smaller than 2 MB.")
            draft = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            if not isinstance(draft, dict):
                raise ValueError("A scenario file must contain a story and a list of stages.")
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not open scenario", str(exc), parent=self.top)
            return
        self.app.cfg["scenario_recovery"] = self.collect()
        self.app._scenario_draft = draft
        self.app._save_cfg()
        self.app._show_scenario_editor()
        self.top.destroy()

    def undo(self):
        if not hasattr(self, "previous"):
            return
        self.plan, self.previous = self.previous, self.collect()
        self.stages = deepcopy(self.plan["stages"])
        self.title.set(self.plan["title"])
        self.premise.delete("1.0", "end")
        self.premise.insert("1.0", self.plan["premise"])
        self.mode.set(self.plan.get("mode", "assisted"))
        self.vanilla.set(bool(self.plan.get("allow_vanilla", False)))
        self.start.set(next((label for label, mid in self.mod_choices.items() if mid == self.plan.get("preferred_start")), ""))
        self.refresh()

    def check(self, show_review=True):
        from quest_scenarios import validate_scenario
        draft = self.collect()
        try:
            report = validate_scenario(self.scan, self.selection, draft)
        except ValueError as exc:
            report = {"valid": False, "stages": [], "issues": [{"message": str(exc)}], "notes": []}
        notes = list(dict.fromkeys([note for note in (draft.get("notes") or []) if isinstance(note, str)] + report.get("notes", [])))
        body = [draft["title"], draft["premise"], "STAGE REVIEW"]
        for stage in report["stages"]:
            plan = stage.get("goal_plan") or {}
            body += ["\n" + stage.get("title", "Untitled stage"),
                     "Goal: " + stage.get("goal", "Choose a goal"),
                     "Completion: " + ("Player confirms the described action" if stage.get("completion") == "manual" else "FTB Quests detects the objective"),
                     stage.get("description", ""), plan.get("explanation", "")]
        if report["issues"]:
            body += ["\nNEEDS ATTENTION"] + ["• " + issue["message"] for issue in report["issues"]]
        body += ["\nAUTHOR NOTES"] + ["• " + note for note in notes]
        self.review_text.configure(state="normal")
        self.review_text.delete("1.0", "end")
        self.review_text.insert("1.0", "\n\n".join(filter(None, body)))
        self.review_text.configure(state="disabled")
        self.feedback.configure(text=(f"{len(report['stages'])} stages checked. Review starting resources and manual actions before building."
                                      if report["valid"] else report["issues"][0]["message"]))
        if show_review:
            self.tabs.select(1)
        return report

    def use(self):
        report = self.check(show_review=False)
        if not report["valid"]:
            self.tabs.select(1)
            return
        self.app._scenario_plan = self.collect()
        self.app._scenario_draft = None
        self.app.opt_campaign.set(True)
        self.app._sync_campaign_controls()
        self.app._save_cfg()
        self.top.destroy()

