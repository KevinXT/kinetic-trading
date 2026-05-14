"""
Task registry and base types for pipeline steps.

The registry maps string task names to callables. Consumer code (apps, task
modules) registers tasks; the runner resolves them by name at execution time.
"""
