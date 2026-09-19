"""Code-defined flow entities.

Each subfolder is a category (processors, conditions, transforms, ...). Drop a
module exposing a module-level ``SPEC = FlowEntitySpec(...)`` and a
``run(state, config, ctx)`` function; ``flow.registry`` discovers it
automatically. See ``flow/registry.py`` for the contract.
"""
