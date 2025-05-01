"""This is Chapter II Desktop, an initiative to create a unified
desktop-environment-like experience for creating, managing, and interacting with
Chapter II ems

Two layout modes will be possible:

- Canvas, where you can lay out multiple windows on an infinite canvas / node editor
- Tile, a traditional that allows docking panels to the side and as tabs, similar to
most IDEs.

Some elements will be shared between both layout modes:

- A status bar, displaying the status of any currently-running language model
continuations, web searches, or representation embedding requests.

Some of the windows that will eventually be added:

- A window for inspecting the state of a currently running language model
- A process manager for managing system daemons
- A window for creating and modifying individual ems like Arago
- A Loom– a window for exploring possible completions using a prefix tree structure
- A window that displays the code corresponding to the option user currently hovers over
"""

import pydantic
from annotated_types import Interval
from imgui_bundle import imgui, immapp
from pydantic.fields import FieldInfo

from load import load_em
from ontology import EmConfig, DragSpeed


# todo: allow annotating in ontology.py which options should show up in which set
# ImGUI docs: https://pthom.github.io/imgui_manual_online/manual/imgui_manual.html


def render_EmConfig(em: EmConfig):
    for field_name, field in em.model_fields.items():
        if field.annotation == str:
            modified, new = imgui.input_text(field_name, getattr(em, field_name))
            if modified:
                setattr(em, field_name, new)
        elif field.annotation == int:
            modified, new = imgui.input_int(field_name, getattr(em, field_name))
            if modified:
                setattr(em, field_name, new)
        elif field.annotation == bool:
            modified, new = imgui.checkbox(field_name, getattr(em, field_name))
            if modified:
                setattr(em, field_name, new)
        elif field.annotation == float:
            # Ctrl-click/Cmd-click to turn into input box
            match field.metadata:
                case [Interval() as interval, *_]:
                    v_min = interval.ge or interval.gt
                    v_max = interval.le or interval.lt
                case _:
                    v_min, v_max = 0, 0
            match field.metadata:
                case [*_, DragSpeed(v_speed)]:
                    modified, new = imgui.drag_float(
                        field_name,
                        getattr(em, field_name),
                        v_speed,
                        v_min,
                        v_max,
                    )
                case _:
                    # Don't make draggers for those without DragSpeed
                    modified, new = imgui.input_float(
                        field_name,
                        getattr(em, field_name),
                    )
            if modified:
                setattr(em, field_name, new)
        elif isinstance(field.annotation, list):
            pass

    return em


class GUI:
    def __init__(self):
        self.em = load_em("arago").em

    def render(self):
        render_EmConfig(self.em)


if __name__ == "__main__":
    immapp.run(
        gui_function=GUI().render,  # The Gui function to run
        window_title="Hello!",  # the window title
        window_size_auto=True,  # Auto size the application window given its widgets
        # Uncomment the next line to restore window position and size from previous run
        # window_restore_previous_geometry==True
    )
