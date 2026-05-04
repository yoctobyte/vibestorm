"""3D viewer fork for the Vibestorm session.

Started as a byte-for-byte copy of ``vibestorm.viewer`` (the 2D bird's-eye
viewer) on 2026-05-04. The 2D viewer remains as a stable reference; this
package is where 3D rendering work happens. A 2D mode will eventually be
re-introduced here behind a renderer abstraction so a single viewer covers
both.

Pure-logic modules (``camera``, ``scene``) are pygame-free and unit-testable.
``app``/``hud``/``input``/``render`` import pygame and can only be loaded in
an environment where the ``viewer`` extra is installed.
"""
