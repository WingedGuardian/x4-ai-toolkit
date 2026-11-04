"""x4validate — X4 Foundations cross-file XML validator.

Checks mod diff patches against the *effective* merged game tree (base + DLC,
optionally + enabled mods):
  1. sel= / if= resolution  — does each diff op match something?
  2. reference integrity     — do ware/macro/component/text refs resolve?
  3. cross-file completeness — modeled on a vanilla analogue, what's missing?

Built on lxml (real XPath) because X4's most common selector idiom (`//ware[...]`)
breaks naive ElementTree-based matchers.
"""

__version__ = "4.1.0"
