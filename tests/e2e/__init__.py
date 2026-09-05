"""The live end-to-end harness: it drives the real seeded workflows in the two demo orgs.

A package (rather than loose test files) so `python -m tests.e2e.cleanup` can be run on its
own after an interrupted run, and so the modules can import each other by relative name.
"""
