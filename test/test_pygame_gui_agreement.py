"""What the code assumes about pygame_gui, checked against pygame_gui.

Twice in this project a screen has been wrong about the library's shape, and
both times the failure surfaced somewhere it read as something else:

- `UIDropDownMenu.selected_option` is a `(display text, object id)` pair, not
  a string. Every `preset == "Second Life"` in the login screen was therefore
  false, and choosing Second Life logged in to `http://127.0.0.1:9000/`.
  Nothing said so -- the local sim answered.
- `UICheckBox.is_checked` is a `bool` attribute; the accessor is
  `get_state()`. `self.remember_checkbox.is_checked()` raised `TypeError`
  inside the arm that runs when a login *succeeds*, so a working login
  reported "Unexpected error: 'bool' object is not callable" and "Remember
  me" never saved anything.

Neither was going to be found by testing harder around the symptom, and the
pin is `pygame_gui>=0.6,<1`, so a library upgrade inside the allowed range can
introduce a third one at any time.

This file asks the general question instead: for every `self.<widget>.<name>(...)`
the source writes, is `<name>` callable on the widget that is actually there?
It is cheap -- one construction per screen -- and it names the widget, the
method and the type it found.

It does not catch the dropdown bug, which is a call that succeeds and returns
the wrong shape; `test_login_screen.py::PresetShapeTests` covers that one
directly. It catches the family the second bug belongs to.

A widget that is only built on demand -- the 3D HUD's file dialog and its
inspector window -- is `None` on a freshly constructed screen, so there is no
live object to ask. Those are resolved statically instead: the assignment
`self.<widget> = UISomething(...)` names the class, and the methods are
checked against the class rather than an instance. Skipping them would have
been the quiet kind of gap, where the count still looks healthy.
"""

import ast
import inspect
import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")


def is_a_widget(value: object) -> bool:
    """Whether this is a pygame_gui element, including a local subclass of one.

    The MRO rather than the type's own module: the 3D HUD's inspector is a
    `HideOnCloseWindow`, a `UIWindow` subclass declared inside the method that
    builds it, so its `__module__` is the HUD's. Reading only the type's own
    module quietly dropped it, and it is one of the larger windows here.
    """
    return any(base.__module__.split(".")[0] == "pygame_gui" for base in type(value).__mro__)


def widgets_of(owner: object) -> dict[str, object]:
    """The attributes of `owner` that are pygame_gui widgets."""
    found: dict[str, object] = {}
    for name in dir(owner):
        if name.startswith("__"):
            continue
        value = getattr(owner, name, None)
        if is_a_widget(value):
            found[name] = value
    return found


def calls_on_widgets(module: object, widget_names: set[str]) -> set[tuple[str, str]]:
    """`(widget, method)` pairs the module calls as `self.<widget>.<method>(...)`."""
    tree = ast.parse(inspect.getsource(module))
    pairs: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        method = node.func
        if not isinstance(method, ast.Attribute):
            continue
        holder = method.value
        if not isinstance(holder, ast.Attribute) or holder.attr not in widget_names:
            continue
        if isinstance(holder.value, ast.Name) and holder.value.id == "self":
            pairs.add((holder.attr, method.attr))
    return pairs


def reads_on_widgets(module: object, widget_names: set[str]) -> set[tuple[str, str]]:
    """`(widget, attribute)` pairs the module *reads* as `self.<widget>.<attribute>`.

    Calls are excluded -- `calls_on_widgets` covers those -- and so is anything
    the module assigns to itself: a screen is allowed to hang its own bookkeeping
    off a widget, and a fresh one would not have it yet.
    """
    tree = ast.parse(inspect.getsource(module))
    assigned: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        targets: list = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign | ast.AugAssign):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Attribute):
                holder = target.value
                if isinstance(holder.value, ast.Name) and holder.value.id == "self":
                    assigned.add((holder.attr, target.attr))

    called = {
        node.func
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    reads: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or node in called:
            continue
        if not isinstance(node.ctx, ast.Load):
            continue
        holder = node.value
        if not isinstance(holder, ast.Attribute) or holder.attr not in widget_names:
            continue
        if isinstance(holder.value, ast.Name) and holder.value.id == "self":
            pair = (holder.attr, node.attr)
            if pair not in assigned:
                reads.add(pair)
    return reads


def widget_classes() -> dict[str, type]:
    """Every `UI*` class pygame_gui exposes, by bare name."""
    import pygame_gui
    import pygame_gui.elements
    import pygame_gui.windows

    classes: dict[str, type] = {}
    for namespace in (pygame_gui, pygame_gui.elements, pygame_gui.windows):
        for name in dir(namespace):
            value = getattr(namespace, name)
            if isinstance(value, type) and name.startswith("UI"):
                classes.setdefault(name, value)
    return classes


def widget_classes_assigned(module: object) -> dict[str, type]:
    """Widgets the source builds but a fresh screen has not built yet.

    `self._file_dialog = UIFileDialog(...)` names the class even when the
    attribute is `None` until someone opens a dialog. A subclass defined in
    the module itself -- `HideOnCloseWindow` -- resolves through the module,
    which is where it lives.
    """
    known = widget_classes()
    found: dict[str, type] = {}
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        called = node.value.func
        name = called.attr if isinstance(called, ast.Attribute) else getattr(called, "id", "")
        cls = known.get(name) or getattr(module, name, None)
        if not isinstance(cls, type):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                found.setdefault(target.attr, cls)
    return found


class _AgreementCase(unittest.TestCase):
    """Point `build()` at a screen and the walk does the rest."""

    __test__ = False

    #: Fewer pairs than this and the walk has stopped finding calls, rather
    #: than the screen having stopped making them.
    MINIMUM_PAIRS = 10

    #: Same floor, for the attribute reads. These numbers are small on
    #: purpose: almost everything these screens do to a widget is a call, and
    #: the handful of bare reads is the finding rather than a defect. A floor
    #: of the exact count is what makes a *lost* read visible.
    MINIMUM_READS = 0

    #: A widget attribute that must be present, so a screen that failed to
    #: build its controls cannot pass by having nothing to check.
    EXPECTED_WIDGET = ""

    #: Widgets this screen only builds on demand, which must therefore be
    #: reached through their class rather than through an instance.
    LAZY_WIDGETS: tuple[str, ...] = ()

    def setUp(self) -> None:
        try:
            import pygame
            import pygame_gui  # noqa: F401 - the whole point
        except ImportError as exc:  # pragma: no cover - optional viewer extra
            self.skipTest(f"viewer dependencies unavailable: {exc}")
        self.pygame = pygame
        pygame.init()
        pygame.display.set_mode((1024, 768))
        self.addCleanup(pygame.quit)

    def build(self):  # pragma: no cover - overridden
        raise NotImplementedError

    def module(self):  # pragma: no cover - overridden
        raise NotImplementedError

    def targets(self) -> dict[str, object]:
        """Every widget attribute this screen's methods can be checked against.

        The live objects first, then the classes of the ones that are built
        only on demand.
        """
        module = self.module()
        widgets = widgets_of(self.build())
        self.assertIn(self.EXPECTED_WIDGET, widgets, f"widgets found: {sorted(widgets)}")
        checkable: dict[str, object] = dict(widget_classes_assigned(module))
        checkable.update(widgets)
        return checkable

    def test_every_widget_method_the_screen_calls_is_callable(self) -> None:
        module = self.module()
        checkable = self.targets()
        pairs = calls_on_widgets(module, set(checkable))
        self.assertGreaterEqual(len(pairs), self.MINIMUM_PAIRS, f"only found {sorted(pairs)}")
        for widget_name, method in sorted(pairs):
            with self.subTest(f"{widget_name}.{method}"):
                target = checkable[widget_name]
                attribute = getattr(target, method, None)
                kind = target.__name__ if isinstance(target, type) else type(target).__name__
                self.assertIsNotNone(attribute, f"{kind} has no {method!r}")
                self.assertTrue(
                    callable(attribute),
                    f"{kind}.{method} is a {type(attribute).__name__}, not something to call",
                )

    def test_every_widget_attribute_the_screen_reads_exists(self) -> None:
        """The other half of the same question.

        A call that names something absent raises `AttributeError`; a *read*
        that names something absent raises it too, and a library upgrade is as
        likely to rename an attribute as to turn one into a method. This does
        not know what any of them should *contain* -- `selected_option` being
        a pair rather than a string passes here, and is pinned by name in
        `test_login_screen.py::PresetShapeTests`.
        """
        module = self.module()
        checkable = self.targets()
        reads = reads_on_widgets(module, set(checkable))
        if not self.MINIMUM_READS:
            self.assertEqual(reads, set(), "this screen has started reading widget attributes")
            self.skipTest("this screen only calls widget methods, it reads no attributes")
        self.assertGreaterEqual(len(reads), self.MINIMUM_READS, f"only found {sorted(reads)}")
        for widget_name, attribute in sorted(reads):
            with self.subTest(f"{widget_name}.{attribute}"):
                target = checkable[widget_name]
                kind = target.__name__ if isinstance(target, type) else type(target).__name__
                self.assertTrue(
                    hasattr(target, attribute),
                    f"{kind} has no {attribute!r}",
                )

    def test_the_lazily_built_widgets_are_reached_too(self) -> None:
        """Anti-vacuity: the static half must actually resolve something the
        live half did not, or it is not adding coverage."""
        if not self.LAZY_WIDGETS:
            self.skipTest("this screen builds all its widgets up front")
        checkable = self.targets()
        for name in self.LAZY_WIDGETS:
            self.assertIsInstance(checkable.get(name), type, f"{name} resolved to no class")


class LoginScreenAgreementTests(_AgreementCase):
    __test__ = True
    EXPECTED_WIDGET = "remember_checkbox"
    MINIMUM_PAIRS = 30
    #: `preset_dropdown.selected_option`, which is where the first of the two
    #: bugs lived. A rename of it would land here.
    MINIMUM_READS = 1

    def setUp(self) -> None:
        super().setUp()
        from test_login_screen import isolate_the_saved_profile

        isolate_the_saved_profile(self)

    def module(self):
        from vibestorm.viewer import login_screen

        return login_screen

    def build(self):
        from vibestorm.viewer.login_screen import LoginScreen

        return LoginScreen((1024, 768))


class Viewer2DHudAgreementTests(_AgreementCase):
    __test__ = True
    EXPECTED_WIDGET = "chat_input"
    MINIMUM_PAIRS = 30

    def module(self):
        from vibestorm.viewer import hud

        return hud

    def build(self):
        from vibestorm.viewer.hud import HUD

        return HUD((1024, 768), on_chat_submit=lambda text: None)


class Viewer3DHudAgreementTests(_AgreementCase):
    __test__ = True
    EXPECTED_WIDGET = "chat_input"
    MINIMUM_PAIRS = 100
    MINIMUM_READS = 3
    LAZY_WIDGETS = ("_file_dialog",)

    def module(self):
        from vibestorm.viewer3d import hud

        return hud

    def build(self):
        from vibestorm.viewer3d.hud import HUD

        return HUD((1024, 768), on_chat_submit=lambda text: None)


if __name__ == "__main__":
    unittest.main()
