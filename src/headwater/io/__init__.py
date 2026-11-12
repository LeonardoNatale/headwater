"""Importers: one reader per format, the stages they share, and the registry of formats.

A reader (`ecospold2`, `lcia`) reads its format into an `InventorySource` or a `MethodSource`
(`source`). The shared stages turn them into a release (`inventory`) or a method folder
(`method`). `formats` lists every format, one line each. `excel` imports studies.
"""
