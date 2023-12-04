"""
An interactive command-line tool to explore compilation pipeline construction.

Execute `xdsl-gui` in your terminal to run it.

Run `textual run xdsl.interactive.app:InputApp --dev` to run in development mode. Please
be sure to install `textual-dev` to run this command.
"""

import os
from collections.abc import Callable
from io import StringIO
from typing import Any, ClassVar

from textual import events, on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import (
    Button,
    Footer,
    Label,
    ListItem,
    ListView,
    TextArea,
)

from xdsl.dialects import builtin
from xdsl.dialects.builtin import ModuleOp
from xdsl.interactive.load_file_screen import LoadFile
from xdsl.ir import MLContext
from xdsl.parser import Parser
from xdsl.passes import ModulePass, PipelinePass
from xdsl.printer import Printer
from xdsl.tools.command_line_tool import get_all_dialects, get_all_passes

from ._pasteboard import pyclip_copy

ALL_PASSES = tuple(sorted((p.name, p) for p in get_all_passes()))
"""Contains the list of xDSL passes."""


def condensed_pass_list(input: builtin.ModuleOp) -> tuple[type[ModulePass], ...]:
    """Returns a tuple of passes (pass name and pass instance) that modify the IR."""

    ctx = MLContext(True)

    for dialect in get_all_dialects():
        ctx.load_dialect(dialect)

    selections: tuple[type[ModulePass], ...] = ()
    for _, value in ALL_PASSES:
        try:
            cloned_module = input.clone()
            cloned_ctx = ctx.clone()
            value().apply(cloned_ctx, cloned_module)
            if not input.is_structurally_equivalent(cloned_module):
                rhs = (*selections, value)
                selections = tuple(rhs)
        except Exception:
            selections = tuple((*selections, value))

    return selections


class OutputTextArea(TextArea):
    """Used to prevent users from being able to alter the Output TextArea."""

    async def _on_key(self, event: events.Key) -> None:
        event.prevent_default()


class InputApp(App[None]):
    """
    Interactive application for constructing compiler pipelines.
    """

    CSS_PATH = "app.tcss"

    BINDINGS = [
        ("d", "toggle_dark", "Toggle dark mode"),
        ("q", "quit_app", "Quit"),
    ]

    SCREENS: ClassVar[dict[str, Screen[Any] | Callable[[], Screen[Any]]]] = {
        "load_file": LoadFile
    }

    INITIAL_IR_TEXT = """
        func.func @hello(%n : index) -> index {
          %two = arith.constant 2 : index
          %res = arith.muli %n, %two : index
          func.return %res : index
        }
        """

    current_module = reactive[ModuleOp | Exception | None](None)
    """
    Reactive variable used to save the current state of the modified Input TextArea
    (i.e. is the Output TextArea).
    """
    pass_pipeline = reactive(tuple[type[ModulePass], ...])
    """Reactive variable that saves the list of selected passes."""

    condense_mode = reactive(False, always_update=True)
    """Reactive boolean."""
    available_pass_list = reactive(tuple[type[ModulePass], ...])
    """
    Reactive variable that saves the list of passes that have an effect on
    current_module.
    """

    input_text_area: TextArea
    """Input TextArea."""
    output_text_area: OutputTextArea
    """Output TextArea."""
    selected_query_label: Label
    """Display selected passes."""
    passes_list_view: ListView
    """ListView displaying the passes available to apply."""

    def __init__(self):
        self.input_text_area = TextArea(id="input")
        self.output_text_area = OutputTextArea(id="output")
        self.passes_list_view = ListView(id="passes_list_view")
        self.selected_query_label = Label("", id="selected_passes_label")

        super().__init__()

    def compose(self) -> ComposeResult:
        """
        Creates the required widgets, events, etc.
        """

        with Horizontal(id="top_container"):
            yield self.passes_list_view
            with Horizontal(id="button_and_selected_horziontal"):
                with Vertical(id="buttons"):
                    yield Button("Copy Query", id="copy_query_button")
                    yield Button("Clear Passes", id="clear_passes_button")
                    yield Button("Condense", id="condense_button")
                    yield Button("Uncondense", id="uncondense_button")
                    yield Button("Remove Last Pass", id="remove_last_pass_button")
                with ScrollableContainer(id="selected_passes"):
                    yield self.selected_query_label
        with Horizontal(id="bottom_container"):
            with Vertical(id="input_container"):
                yield self.input_text_area
                with Horizontal(id="input_horizontal"):
                    yield Button("Clear Input", id="clear_input_button")
                    yield Button("Load File", id="load_file_button")
            with Vertical(id="output_container"):
                yield self.output_text_area
                yield Button("Copy Output", id="copy_output_button")
        yield Footer()

    def on_mount(self) -> None:
        """Configure widgets in this application before it is first shown."""
        # Registers the theme for the Input/Output TextAreas
        self.input_text_area.theme = "vscode_dark"
        self.output_text_area.theme = "vscode_dark"

        # add titles for various widgets
        self.query_one("#input_container").border_title = "Input xDSL IR"
        self.query_one("#output_container").border_title = "Output xDSL IR"
        self.query_one(
            "#passes_list_view"
        ).border_title = "Choose a pass or multiple passes to be applied."
        self.query_one("#selected_passes").border_title = "Selected passes/query"

        # initialize ListView to contain the pass options
        for n, _ in ALL_PASSES:
            self.passes_list_view.append(ListItem(Label(n), name=n))

        # initialize GUI with an interesting input IR and pass application
        self.input_text_area.load_text(InputApp.INITIAL_IR_TEXT)

    def compute_available_pass_list(self) -> tuple[type[ModulePass], ...]:
        """
        When any reactive variable is modified, this function (re-)computes the
        available_pass_list variable.
        """
        match self.current_module:
            case None:
                return tuple(p for _, p in ALL_PASSES)
            case Exception():
                return ()
            case ModuleOp():
                if self.condense_mode:
                    return condensed_pass_list(self.current_module)
                else:
                    return tuple(p for _, p in ALL_PASSES)

    def watch_available_pass_list(
        self,
        old_pass_list: tuple[type[ModulePass], ...],
        new_pass_list: tuple[type[ModulePass], ...],
    ) -> None:
        """
        Function called when the reactive variable available_pass_list changes - updates
        the ListView to display the latest pass options.
        """
        if old_pass_list != new_pass_list:
            self.passes_list_view.clear()
            for value in new_pass_list:
                self.passes_list_view.append(
                    ListItem(Label(value.name), name=value.name)
                )

    @on(ListView.Selected)
    def update_pass_pipeline(self, event: ListView.Selected) -> None:
        """
        When a new selection is made, the reactive variable storing the list of selected
        passes is updated.
        """
        selected_pass = event.item.name
        for name, value in ALL_PASSES:
            if name == selected_pass:
                self.pass_pipeline = tuple((*self.pass_pipeline, value))
                return

    def watch_pass_pipeline(self) -> None:
        """
        Function called when the reactive variable pass_pipeline changes - updates the
        label to display the respective generated query in the Label.
        """
        self.selected_query_label.update(self.get_query_string())
        self.update_current_module()

    @on(TextArea.Changed, "#input")
    def update_current_module(self) -> None:
        """
        Function to parse the input and to apply the list of selected passes to it.
        """
        input_text = self.input_text_area.text
        if (input_text) == "":
            self.current_module = None
            self.current_condensed_pass_list = ()
            return
        try:
            ctx = MLContext(True)
            for dialect in get_all_dialects():
                ctx.load_dialect(dialect)
            parser = Parser(ctx, input_text)
            module = parser.parse_module()
            pipeline = PipelinePass([p() for p in self.pass_pipeline])
            pipeline.apply(ctx, module)
            self.current_module = module
        except Exception as e:
            self.current_module = e

    def watch_current_module(self):
        """
        Function called when the reactive variable current_module changes - updates the
        Output TextArea.
        """
        match self.current_module:
            case None:
                output_text = "No input"
            case Exception() as e:
                output_stream = StringIO()
                Printer(output_stream).print(e)
                output_text = output_stream.getvalue()
            case ModuleOp():
                output_stream = StringIO()
                Printer(output_stream).print(self.current_module)
                output_text = output_stream.getvalue()

        self.output_text_area.load_text(output_text)

    def get_query_string(self) -> str:
        """
        Function returning a string containing the textual description of the pass
        pipeline generated thus far.
        """
        new_passes = "\n" + (", " + "\n").join(p.name for p in self.pass_pipeline)
        new_label = f"xdsl-opt -p {new_passes}"
        return new_label

    def action_toggle_dark(self) -> None:
        """An action to toggle dark mode."""
        self.dark = not self.dark

    def action_quit_app(self) -> None:
        """An action to quit the app."""
        self.exit()

    @on(Button.Pressed, "#clear_input_button")
    def clear_input(self, event: Button.Pressed) -> None:
        """Input TextArea is cleared when "Clear Input" button is pressed."""
        self.input_text_area.clear()

    @on(Button.Pressed, "#copy_output_button")
    def copy_output(self, event: Button.Pressed) -> None:
        """Output TextArea is copied when "Copy Output" button is pressed."""
        pyclip_copy(self.output_text_area.text)

    @on(Button.Pressed, "#copy_query_button")
    def copy_query(self, event: Button.Pressed) -> None:
        """Selected passes/query Label is copied when "Copy Query" button is pressed."""
        pyclip_copy(self.get_query_string())

    @on(Button.Pressed, "#clear_passes_button")
    def clear_passes(self, event: Button.Pressed) -> None:
        """Selected passes cleared when "Clear Passes" button is pressed."""
        self.pass_pipeline = ()

    @on(Button.Pressed, "#condense_button")
    def condense(self, event: Button.Pressed) -> None:
        """
        Displayed passes are filtered to display only those passes that have an affect
        on current_module when "Condense" Button is pressed.
        """
        self.condense_mode = True
        self.add_class("condensed")

    @on(Button.Pressed, "#uncondense_button")
    def uncondense(self, event: Button.Pressed) -> None:
        """
        Displayed passes are filtered to display all available passes when "Uncondense"
        Button is pressed.
        """
        self.condense_mode = False
        self.remove_class("condensed")

    @on(Button.Pressed, "#remove_last_pass_button")
    def remove_last_pass(self, event: Button.Pressed) -> None:
        """Last selected pass removed when "Remove Last Pass" button is pressed."""
        self.pass_pipeline = self.pass_pipeline[:-1]

    @on(Button.Pressed, "#load_file_button")
    def load_file(self, event: Button.Pressed) -> None:
        """
        Pushes screen displaying DirectoryTree widget when "Load File" button is pressed.
        """

        def check_load_file(file_path: str) -> None:
            """
            Called when LoadFile is dismissed. Loads selected file into
            input_text_area.
            """
            # Clear Input TextArea and Pass Pipeline
            self.pass_pipeline = ()
            self.input_text_area.clear()

            try:
                if os.path.exists(file_path):
                    # Open the file and read its contents
                    with open(file_path) as file:
                        file_contents = file.read()
                        self.input_text_area.load_text(file_contents)
                else:
                    self.input_text_area.load_text(
                        f"The file '{file_path}' does not exist."
                    )
            except Exception as e:
                self.input_text_area.load_text(str(e))

        self.push_screen("load_file", check_load_file)


def main():
    return InputApp().run()


if __name__ == "__main__":
    main()