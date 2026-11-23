"""olayout - a 2D, to-scale optical layout engine for Thorlabs breadboards.

Package layout
--------------
core      Qt-free model: units, geometry, breadboard, layout document, validation.
catalog   Thorlabs part definitions (3D primitive models) and combined units.
optics    Gaussian beam math and the 2D beam tracer.
render    One-time top-down rendering of part models into sprites.
ui        PySide6 application (canvas, panels, tools).

All lengths inside the engine are in inches; angles are in degrees,
counter-clockwise, with +y pointing "up" (away from the user) on the table.
"""

__version__ = "0.1.0"
