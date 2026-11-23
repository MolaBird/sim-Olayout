"""One-time, top-down rendering of 3D part models into 2D sprites.

The canvas never renders 3D: each (part, orientation) is rasterized once into
an RGBA image plus a soft shadow mask and cached (in memory and on disk).
"""
