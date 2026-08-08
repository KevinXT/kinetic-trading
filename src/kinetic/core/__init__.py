"""Platform mechanics: pipeline execution, configuration, artifacts, provenance, errors.

``kinetic.core`` knows how to *run* things. It does not know what a price bar is,
which providers exist, or what a research result means. It must not import any
other ``kinetic`` subsystem.
"""
