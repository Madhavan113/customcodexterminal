"""Keyboard-first terminal UI. Background updates never replace a user's draft."""

from datetime import UTC, datetime
from typing import ClassVar

from rich.syntax import Syntax
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Input,
    Label,
    OptionList,
    RichLog,
    Select,
    SelectionList,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)
from textual.widgets.option_list import Option

from .rpc import RpcError, local_call
from .store import clean

COLORS = {
    "working": "#5CE0A5",
    "waiting": "#FFE68F",
    "failed": "#FF5C8A",
    "disconnected": "#FF8FB1",
    "starting": "#4FDDFF",
    "ready": "#96AEFF",
    "interrupted": "#7E7A9C",
    "interrupting": "#FFD75C",
}


class NewAgent(ModalScreen):
    BINDINGS: ClassVar = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "submit", "Start agent", priority=True),
    ]

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog"):
            yield Label("NEW AGENT", classes="dialog-title")
            yield Select(
                [("Codex", "codex"), ("Claude", "claude")],
                value="codex",
                allow_blank=False,
                id="provider",
            )
            yield Input(
                placeholder="Task name, e.g. Fix the login flow",
                id="task-name",
                max_length=80,
            )
            yield Input(
                placeholder="Model (blank uses your existing settings)", id="model"
            )
            yield TextArea(placeholder="What should this agent do?", id="task-prompt")
            yield Checkbox("Separate Git worktree (starts at HEAD)", id="worktree")
            yield Static(
                "Shared folder uses your current files, including uncommitted changes.",
                classes="hint",
            )
            with Horizontal(classes="dialog-actions"):
                yield Button("Start agent", id="start-agent", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self):
        self.query_one("#task-name", Input).focus()

    @on(Button.Pressed, "#start-agent")
    def action_submit(self):
        title = self.query_one("#task-name", Input).value.strip()
        prompt = self.query_one("#task-prompt", TextArea).text
        if not title or not prompt.strip():
            self.notify(
                "Give the agent a task name and instructions", severity="warning"
            )
            return
        self.dismiss(
            {
                "provider": self.query_one("#provider", Select).value,
                "title": title,
                "prompt": prompt,
                "model": self.query_one("#model", Input).value.strip() or None,
                "isolated": self.query_one("#worktree", Checkbox).value,
            }
        )

    @on(Button.Pressed, "#cancel")
    def action_cancel(self):
        self.dismiss(None)


class AttentionDetail(ModalScreen):
    BINDINGS: ClassVar = [Binding("escape", "cancel", "Back")]

    def __init__(self, entry):
        super().__init__()
        self.entry = entry

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog"):
            yield Label(Text(clean(self.entry["title"])), classes="dialog-title")
            data = self.entry["data"]
            if data.get("detail"):
                yield Static(Text(clean(data["detail"])), id="request-detail")
            for index, question in enumerate(data.get("questions", [])):
                yield Label(Text(clean(question.get("question", "Your answer"))))
                options = question.get("options") or []
                choices = [
                    (Text(clean(item["label"])), item["label"]) for item in options
                ]
                if choices:
                    if question.get("multiSelect"):
                        yield SelectionList(*choices, id=f"choices-{index}")
                    else:
                        yield Select(
                            choices, prompt="Choose an answer", id=f"choices-{index}"
                        )
                yield Input(
                    placeholder="Your answer"
                    if not choices
                    else "Or type your own answer",
                    password=question.get("isSecret", False),
                    id=f"answer-{index}",
                )
            with Horizontal(classes="dialog-actions"):
                if (
                    self.entry["status"] == "pending"
                    and self.entry["kind"] == "approval"
                ):
                    yield Button("Allow once", id="allow", variant="primary")
                    yield Button("Deny", id="deny", variant="error")
                elif self.entry["status"] == "pending":
                    yield Button("Send answer", id="answer", variant="primary")
                else:
                    yield Button("Dismiss", id="dismiss", variant="primary")
                yield Button("Back", id="back")

    def action_cancel(self):
        self.dismiss(None)

    @on(Button.Pressed)
    def handle_button(self, event):
        button = event.button.id
        if button == "back":
            self.action_cancel()
        elif button in {"allow", "deny", "dismiss"}:
            self.dismiss({"decision": button})
        elif button == "answer":
            answers = {}
            for index, question in enumerate(self.entry["data"].get("questions", [])):
                value = self.query_one(f"#answer-{index}", Input).value.strip()
                if not value and question.get("options"):
                    if question.get("multiSelect"):
                        value = self.query_one(
                            f"#choices-{index}", SelectionList
                        ).selected
                    else:
                        selected = self.query_one(f"#choices-{index}", Select).value
                        value = selected if selected is not Select.NULL else ""
                if not value:
                    self.notify(
                        "Answer every question before sending", severity="warning"
                    )
                    return
                answers[question["id"]] = value
            self.dismiss({"answers": answers})


class WorkspaceApp(App):
    TITLE = "Noir"
    CSS = """
    Screen { background: #141320; color: #ECEAF5; }
    #topbar { height: 2; padding: 0 1; background: #201D32; color: #D89DFF; }
    #workspace { height: 1fr; }
    #sidebar { width: 29; min-width: 20; border-right: solid #4A3D78; padding: 0 1; }
    #sidebar-title { height: 2; color: #7E7A9C; padding-top: 1; }
    #agents { height: 1fr; background: #141320; scrollbar-size: 1 1; border: none; padding: 0; }
    #new-button { width: 100%; min-width: 0; margin: 0; }
    #main { width: 1fr; min-width: 20; }
    #agent-detail { height: 3; padding: 0 1; }
    #tabs { height: 1fr; }
    TabPane { padding: 0 1; }
    RichLog { background: #141320; scrollbar-size: 1 1; }
    #conversation, #activity { height: 1fr; }
    #scope { width: 29; }
    #scope-label { height: 2; color: #96AEFF; }
    #diff-layout { height: 1fr; }
    #files { width: 28; background: #141320; border-right: solid #4A3D78; scrollbar-size: 1 1; }
    #diff { width: 1fr; }
    #attention-label { height: 1; background: #292337; color: #FFE68F; padding: 0 1; }
    #inbox { height: 3; max-height: 5; background: #201D32; padding: 0 1; border: none; scrollbar-size: 1 1; }
    #composer-row { height: 5; }
    #composer { width: 1fr; height: 5; border: solid #4A3D78; background: #191725; }
    #controls { width: 17; }
    #send, #interrupt { width: 100%; min-width: 0; height: 2; min-height: 2; margin: 0; border: none; }
    #message-target { height: 1; color: #96AEFF; padding: 0 1; }
    Footer { background: #201D32; }
    Button { background: #2B2840; color: #ECEAF5; }
    Button.-primary { background: #4A3D78; color: #FFFFFF; }
    Button.-error { background: #582C43; color: #FF8FB1; }
    OptionList > .option-list--option-highlighted { background: #4A3D78; color: #FFFFFF; }
    Tabs { background: #191725; }
    .dialog { width: 78; max-width: 94%; height: auto; max-height: 94%; padding: 1 2; border: solid #96AEFF; background: #201D32; }
    ModalScreen { align: center middle; background: #141320 75%; }
    .dialog-title { color: #D89DFF; margin-bottom: 1; }
    .dialog Input, .dialog Select { margin-bottom: 1; }
    #task-prompt { height: 7; background: #141320; }
    .dialog-actions { height: auto; margin-top: 1; }
    .dialog-actions Button { margin-right: 1; min-width: 10; }
    .hint { color: #96AEFF; height: auto; margin-top: 1; }
    #request-detail { height: auto; margin-bottom: 1; }
    .dialog SelectionList { height: auto; max-height: 8; }
    .narrow #files { width: 23; }
    .narrow #sidebar { width: 24; }
    .compact #workspace { layout: vertical; }
    .compact #sidebar { width: 100%; height: 6; border-right: none; border-bottom: solid #4A3D78; }
    .compact #sidebar-title, .compact #new-button { display: none; }
    .compact #main { width: 100%; height: 1fr; }
    .compact #agent-detail { height: 2; }
    .compact #diff-layout { layout: vertical; }
    .compact #files { width: 100%; height: 3; border-right: none; }
    .compact #diff { height: 1fr; width: 100%; }
    .compact #composer-row, .compact #composer { height: 3; }
    .compact #controls { width: 13; }
    .compact #interrupt { display: none; }
    .hide-sidebar #sidebar { display: none; }
    .short #topbar { height: 1; }
    .short #agent-detail { height: 2; }
    .short #inbox { height: 1; }
    .short #composer-row, .short #composer { height: 3; }
    .short #send, .short #interrupt { height: 1; min-height: 1; padding: 0; }
    .compact.short #sidebar { height: 4; }
    .compact.short #agent-detail { height: 1; }
    .compact.short #scope-label { height: 1; }
    .compact.short #files { height: 2; border: none; padding: 0; }
    """
    BINDINGS: ClassVar = [
        Binding("ctrl+n", "new_agent", "New agent", priority=True),
        Binding("ctrl+s", "send_message", "Send", priority=True),
        Binding("f8", "interrupt_agent", "Interrupt", priority=True),
        Binding("ctrl+g", "changes_tab", "Changes", priority=True),
        Binding("f6", "attention", "Inbox", priority=True),
        Binding("f2", "sidebar", "Agents", show=False, priority=True),
        Binding("ctrl+l", "focus_message", "Message", show=False, priority=True),
        Binding("ctrl+q", "quit", "Detach", priority=True),
    ]

    def __init__(self, project, socket, client=None):
        super().__init__()
        self.project = project
        self.socket = socket
        self.client = client or (
            lambda method, params=None: local_call(
                socket,
                method,
                params,
                timeout=60 if method in {"send", "changes"} else 15,
            )
        )
        self.selected = None
        self.snapshot_data = {"agents": [], "inbox": [], "events": []}
        self.drafts = {}
        self.tree_nodes = {}
        self.last_structure = None
        self.last_events = None
        self.last_inbox = None
        self.last_diff = None
        self.last_files = None
        self.selected_file = None
        self.scope = "task"
        self.polling = False
        self.sending = False

    def compose(self) -> ComposeResult:
        yield Static("NOIR", id="topbar")
        with Horizontal(id="workspace"):
            with Vertical(id="sidebar"):
                yield Static("AGENTS", id="sidebar-title")
                yield OptionList(id="agents", markup=False)
                yield Button("New agent", id="new-button")
            with Vertical(id="main"):
                yield Static("Start an agent to see its work here.", id="agent-detail")
                with TabbedContent(id="tabs"):
                    with TabPane("Conversation", id="conversation-tab"):
                        yield RichLog(
                            wrap=True, min_width=1, max_lines=5000, id="conversation"
                        )
                    with TabPane("Changes", id="changes-tab"):
                        yield Select(
                            [("Since agent started", "task"), ("Git changes", "git")],
                            value="task",
                            allow_blank=False,
                            id="scope",
                            compact=True,
                        )
                        yield Static("", id="scope-label")
                        with Horizontal(id="diff-layout"):
                            yield OptionList(id="files", markup=False)
                            yield RichLog(min_width=1, max_lines=5000, id="diff")
                    with TabPane("Activity", id="activity-tab"):
                        yield RichLog(
                            wrap=True, min_width=1, max_lines=5000, id="activity"
                        )
        yield Static("ATTENTION · Nothing waiting", id="attention-label")
        yield OptionList(id="inbox", markup=False)
        yield Static("Ctrl+N starts a new agent", id="message-target")
        with Horizontal(id="composer-row"):
            yield TextArea(
                placeholder="Message the selected agent · Ctrl+S to send", id="composer"
            )
            with Vertical(id="controls"):
                yield Button("Send", id="send", variant="primary", disabled=True)
                yield Button("Interrupt", id="interrupt", disabled=True)
        yield Footer()

    async def on_mount(self):
        self.animation_level = "none"
        self.main_screen = self.screen
        self.agent_tree = self.query_one("#agents", OptionList)
        self.composer = self.query_one("#composer", TextArea)
        self.tabs = self.query_one("#tabs", TabbedContent)
        self.inbox_widget = self.query_one("#inbox", OptionList)
        self.file_widget = self.query_one("#files", OptionList)
        self.inbox_widget.display = False
        self.agent_tree.focus()
        await self.poll()
        self.set_interval(0.6, self.poll)

    def get_system_commands(self, _screen):
        yield SystemCommand(
            "New agent", "Assign a task to Codex or Claude", self.action_new_agent
        )
        yield SystemCommand(
            "Message agent",
            "Focus the selected agent's draft",
            self.action_focus_message,
        )
        yield SystemCommand(
            "Interrupt agent",
            "Request interruption of the selected active run",
            self.action_interrupt_agent,
        )
        yield SystemCommand(
            "Review changes",
            "Compare task changes or inspect Git status",
            self.action_changes_tab,
        )
        yield SystemCommand(
            "Attention inbox",
            "Questions, permissions and results",
            self.action_attention,
        )
        yield SystemCommand(
            "Toggle agents", "Give the current view more room", self.action_sidebar
        )
        yield SystemCommand(
            "Detach", "Leave agents running and return to your shell", self.action_quit
        )

    def check_action(self, action, parameters):
        return not (
            isinstance(self.screen, ModalScreen)
            and action
            in {
                "new_agent",
                "send_message",
                "interrupt_agent",
                "changes_tab",
                "attention",
                "sidebar",
                "focus_message",
            }
        )

    def on_resize(self, event):
        screen = getattr(self, "main_screen", self.screen)
        screen.set_class(event.size.width < 100, "narrow")
        screen.set_class(event.size.width < 70, "compact")
        screen.set_class(event.size.height < 29, "short")

    async def poll(self):
        if self.polling or not self.is_running:
            return
        self.polling = True
        try:
            data = await self.client("snapshot", {"selected": self.selected})
            if not self.is_running:
                return
            self.snapshot_data = data
            self.render_snapshot(data)
            if self.selected and self.tabs.active == "changes-tab":
                selection = (self.selected, self.scope, self.selected_file)
                result = await self.client(
                    "changes",
                    {
                        "agent_id": selection[0],
                        "scope": selection[1],
                        "selected": selection[2],
                    },
                )
                if self.is_running and selection == (
                    self.selected,
                    self.scope,
                    self.selected_file,
                ):
                    self.render_changes(result)
        except (OSError, RpcError, TimeoutError) as error:
            if self.is_running:
                self.main_screen.query_one("#topbar", Static).update(
                    Text(f"NOIR · Disconnected: {clean(error, 120)}")
                )
        finally:
            self.polling = False

    def selected_agent(self):
        return next(
            (
                agent
                for agent in self.snapshot_data["agents"]
                if agent["id"] == self.selected
            ),
            None,
        )

    def save_draft(self):
        if self.selected:
            self.drafts[self.selected] = (
                self.composer.text,
                self.composer.cursor_location,
            )

    def select_agent(self, agent_id):
        if self.selected == agent_id:
            return
        self.save_draft()
        self.selected = agent_id
        draft, cursor = self.drafts.get(agent_id, ("", (0, 0)))
        self.composer.load_text(draft)
        self.composer.move_cursor(cursor)
        self.selected_file = None
        self.last_events = self.last_files = self.last_diff = None

    def render_snapshot(self, data):
        agents = data["agents"]
        structure = [(agent["id"], agent.get("parent")) for agent in agents]
        if structure != self.last_structure:
            self.agent_tree.clear_options()
            self.tree_nodes = {}

            def add_children(parent, depth=0):
                for item in agents:
                    if (
                        item.get("parent") == parent
                        and item["id"] not in self.tree_nodes
                    ):
                        self.tree_nodes[item["id"]] = depth
                        self.agent_tree.add_option(Option("", id=item["id"]))
                        add_children(item["id"], depth + 1)

            add_children(None)
            if self.selected in self.tree_nodes:
                self.agent_tree.highlighted = list(self.tree_nodes).index(self.selected)
            self.last_structure = structure
        for agent in agents:
            if agent["id"] in self.tree_nodes:
                depth = self.tree_nodes[agent["id"]]
                indent = "  " * min(depth, 3)
                label = Text(
                    f"{indent}{'↳ ' if depth else ''}{agent['provider'].title()} · {agent['title']}\n",
                    style="bold" if agent["id"] == self.selected else "",
                )
                label.append(
                    f"{indent}{agent['status']}",
                    style=COLORS.get(agent["status"], "#ECEAF5"),
                )
                self.agent_tree.replace_option_prompt(agent["id"], label)
        if not self.selected or self.selected not in self.tree_nodes:
            self.select_agent(data.get("selected"))
            if self.selected in self.tree_nodes:
                self.agent_tree.highlighted = list(self.tree_nodes).index(self.selected)
        waiting = sum(agent["status"] == "waiting" for agent in agents)
        active = sum(
            agent["status"] in {"working", "starting", "interrupting"}
            for agent in agents
        )
        self.main_screen.query_one("#topbar", Static).update(
            Text(f"NOIR   {self.project.name}     {active} working · {waiting} waiting")
        )
        agent = self.selected_agent()
        if agent:
            detail = Text(
                f"{agent['title']}  ·  {agent['provider'].title()}  ·  {agent.get('model') or 'configured model'}\n",
                style="bold",
            )
            detail.append(
                f"{agent['status']}  ·  {agent['detail']}\n",
                style=COLORS.get(agent["status"], "#ECEAF5"),
            )
            detail.append(clean(agent["cwd"]), style="#96AEFF")
            self.main_screen.query_one("#agent-detail", Static).update(detail)
            target = (
                f"Message {agent['title']}"
                if agent.get("can_message", True)
                else f"Message the parent about {agent['title']}"
            )
            self.main_screen.query_one("#message-target", Static).update(Text(target))
            self.main_screen.query_one("#send", Button).disabled = (
                self.sending or agent["status"] in {"starting", "interrupting"}
            )
            self.main_screen.query_one("#interrupt", Button).disabled = not (
                agent.get("can_interrupt", True)
                and agent["status"] in {"working", "waiting"}
            )
        events_key = (
            self.selected,
            [
                (event["id"], event["kind"], event["message"], event["data"])
                for event in data["events"]
            ],
        )
        if events_key != self.last_events and data.get("selected") == self.selected:
            self.render_events(data["events"])
            self.last_events = events_key
        inbox_key = [
            (entry["id"], entry["status"], entry["title"]) for entry in data["inbox"]
        ]
        if inbox_key != self.last_inbox:
            highlighted = self.inbox_widget.highlighted
            self.inbox_widget.clear_options()
            lookup = {agent["id"]: agent["title"] for agent in agents}
            for entry in data["inbox"]:
                self.inbox_widget.add_option(
                    Option(
                        Text(
                            f"{lookup.get(entry['agent'], 'Agent')} · {entry['title']}"
                        ),
                        id=entry["id"],
                    )
                )
            self.inbox_widget.display = bool(data["inbox"])
            if data["inbox"]:
                self.inbox_widget.highlighted = min(
                    highlighted or 0, len(data["inbox"]) - 1
                )
            self.main_screen.query_one("#attention-label", Static).update(
                f"ATTENTION · {len(data['inbox'])} item(s) · F6 to open"
                if data["inbox"]
                else "ATTENTION · Nothing waiting"
            )
            self.last_inbox = inbox_key
        if not agents:
            log = self.main_screen.query_one("#conversation", RichLog)
            if not log.lines:
                log.write(
                    Text(
                        "Your agents will appear here.\n\nCtrl+N  Assign a task to Codex or Claude\nCtrl+P  Open the command palette\nCtrl+Q  Detach; agents keep running",
                        style="#96AEFF",
                    )
                )

    @staticmethod
    def replace_log(log, contents):
        position, at_end = log.scroll_y, log.is_vertical_scroll_end
        log.clear()
        for content in contents:
            log.write(content, scroll_end=False)
        if at_end:
            log.call_after_refresh(log.scroll_end, animate=False)
        else:
            log.call_after_refresh(log.scroll_to, y=position, animate=False)

    def render_events(self, events):
        conversation, activity = [], []
        for event in events:
            if event["kind"] in {"user", "assistant", "status", "control", "error"}:
                label = {"user": "YOU", "assistant": "AGENT"}.get(
                    event["kind"], event["kind"].upper()
                )
                row = Text(
                    f"{label}\n",
                    style="#D89DFF" if event["kind"] == "user" else "#96AEFF",
                )
                row.append(clean(event["message"]) + "\n", style="#ECEAF5")
                conversation.append(row)
            if event["kind"] not in {"assistant", "user", "diff", "diagnostic"}:
                stamp = (
                    datetime.fromtimestamp(event["stamp"], tz=UTC)
                    .astimezone()
                    .strftime("%H:%M:%S")
                )
                row = Text(f"{stamp}  {event['kind'].upper()}\n", style="#96AEFF")
                row.append(clean(event["message"]) + "\n", style="#ECEAF5")
                detail = event["data"].get("output") or event["data"].get("detail")
                if detail:
                    row.append(clean(detail) + "\n", style="#96AEFF")
                activity.append(row)
        self.replace_log(
            self.main_screen.query_one("#conversation", RichLog), conversation
        )
        self.replace_log(self.main_screen.query_one("#activity", RichLog), activity)

    def render_changes(self, result):
        files = result["files"]
        scope_label = result.get("scope_label", "")
        if self.main_screen.has_class("compact"):
            scope_label = scope_label.replace("Since agent started · ", "Task · ")
        self.main_screen.query_one("#scope-label", Static).update(
            Text(
                f"{scope_label}\n{result.get('branch', '')} · {len(files)} changed files"
            )
        )
        file_key = [
            (item["path"], item["status"], item.get("added"), item.get("removed"))
            for item in files
        ]
        if file_key != self.last_files:
            self.file_widget.clear_options()
            for index, item in enumerate(files):
                text = Text(f"{item['status']} {clean(item['path'])}")
                if item.get("added") is not None:
                    text.append(
                        f"\n  +{item['added']} −{item['removed']}", style="#8DF1C6"
                    )
                self.file_widget.add_option(Option(text, id=f"file-{index}"))
            self.last_files = file_key
            self.file_paths = [item["path"] for item in files]
            if result.get("selected") in self.file_paths:
                self.file_widget.highlighted = self.file_paths.index(result["selected"])
        self.selected_file = result.get("selected")
        diff_key = (self.selected, self.scope, self.selected_file, result["diff"])
        if diff_key != self.last_diff:
            self.replace_log(
                self.main_screen.query_one("#diff", RichLog),
                [
                    Syntax(
                        result["diff"],
                        "diff",
                        theme="ansi_dark",
                        word_wrap=False,
                        background_color="#141320",
                    )
                ],
            )
            self.last_diff = diff_key

    @on(OptionList.OptionHighlighted, "#agents")
    def agent_selected(self, event):
        if event.option.id:
            self.select_agent(event.option.id)
            self.run_worker(self.poll())

    @on(OptionList.OptionSelected, "#agents")
    def agent_opened(self):
        self.action_focus_message()

    @on(OptionList.OptionSelected, "#inbox")
    def inbox_selected(self, event):
        entry = next(
            (
                item
                for item in self.snapshot_data["inbox"]
                if item["id"] == event.option.id
            ),
            None,
        )
        if entry:
            self.push_screen(
                AttentionDetail(entry),
                lambda answer: (
                    self.run_worker(self.resolve_request(entry, answer))
                    if answer
                    else None
                ),
            )

    @on(OptionList.OptionHighlighted, "#files")
    def file_selected(self, event):
        paths = getattr(self, "file_paths", [])
        if (
            event.option_index < len(paths)
            and paths[event.option_index] != self.selected_file
        ):
            self.selected_file = paths[event.option_index]
            self.run_worker(self.poll())

    @on(Select.Changed, "#scope")
    def scope_changed(self, event):
        if event.value in {"task", "git"} and self.scope != event.value:
            self.scope = event.value
            self.selected_file = None
            self.last_files = self.last_diff = None
            self.run_worker(self.poll())

    @on(Button.Pressed, "#new-button")
    def action_new_agent(self):
        self.push_screen(
            NewAgent(),
            lambda data: self.run_worker(self.launch_agent(data)) if data else None,
        )

    async def launch_agent(self, data):
        try:
            result = await self.client("launch", data)
            self.select_agent(result["agent_id"])
            await self.poll()
        except (OSError, RpcError, TimeoutError) as error:
            self.notify(clean(error), severity="error")

    @on(Button.Pressed, "#send")
    async def action_send_message(self):
        if not self.selected or self.sending or not self.composer.text.strip():
            return
        selected, prompt = self.selected, self.composer.text
        self.sending = True
        try:
            result = await self.client("send", {"agent_id": selected, "prompt": prompt})
            # A user may switch agents or keep typing while a request is in flight.
            if self.selected == selected and self.composer.text == prompt:
                self.composer.clear()
                self.drafts[selected] = ("", (0, 0))
            elif (
                self.selected != selected
                and self.drafts.get(selected, (None,))[0] == prompt
            ):
                self.drafts[selected] = ("", (0, 0))
            self.notify(result.get("message", "Message sent"))
            await self.poll()
        except (OSError, RpcError, TimeoutError) as error:
            self.notify(clean(error), severity="error")
        finally:
            self.sending = False

    @on(Button.Pressed, "#interrupt")
    async def action_interrupt_agent(self):
        if self.selected:
            try:
                result = await self.client("interrupt", {"agent_id": self.selected})
                self.notify(result["message"])
                await self.poll()
            except (OSError, RpcError, TimeoutError) as error:
                self.notify(clean(error), severity="warning")

    async def resolve_request(self, entry, answer):
        try:
            await self.client("resolve", {"request_id": entry["id"], "answer": answer})
            await self.poll()
        except (OSError, RpcError, TimeoutError) as error:
            self.notify(clean(error), severity="error")

    def action_changes_tab(self):
        self.tabs.active = "changes-tab"
        if self.main_screen.has_class("compact"):
            self.main_screen.add_class("hide-sidebar")
        self.run_worker(self.poll())

    def action_attention(self):
        if self.snapshot_data["inbox"]:
            self.inbox_widget.focus()
        else:
            self.notify("Nothing needs your attention")

    def action_sidebar(self):
        self.main_screen.toggle_class("hide-sidebar")
        if not self.main_screen.has_class("hide-sidebar"):
            self.agent_tree.focus()

    def action_focus_message(self):
        self.composer.focus()
