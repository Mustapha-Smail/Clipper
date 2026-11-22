"""Trusted, standalone Blender-side renderer code.

This package deliberately does not import application modules.  Blender runs
``render_scene.py`` with its bundled Python, while the application-side bridge
lives in :mod:`clipper.studio.blender_render`.
"""
