"""2D bird's-eye viewer for the Vibestorm session.

Pure-logic modules (``camera``, ``scene``) are pygame-free and unit-testable.
``app``/``hud``/``input``/``render`` import pygame and can only be loaded in
an environment where the ``viewer`` extra is installed.
"""
