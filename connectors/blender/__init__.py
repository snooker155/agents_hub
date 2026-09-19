"""
Blender geometry connector.

Headless Blender as an external geometry engine, reached only through a
whitelisted command protocol. Agents never write Blender Python: they call
structured geometry operations, which is what makes the engine safe to expose.

Modules:
  - ``store``    — connector config (binary path, mode, ceilings) in .agents_hub/
  - ``protocol`` — the command whitelist and argument validation, stdlib-only so
                   Blender's own interpreter can import it
  - ``host/``    — the script that runs inside Blender; the only place with bpy
  - ``daemon``   — one engine process and the client that drives it
  - ``pool``     — how many engines run, for whom, and who may start one
"""
