"""What SimaPro's product text says about the process that makes it.

SimaPro names a process by the text of its product, and a line names its supplier by that same
text. ecoinvent's processes in SimaPro, and Agribalyse's, write the location in braces:

- `Electricity, medium voltage {FR}| market for | Cut-off, U` gives the product `Electricity,
  medium voltage`, the location `FR` and the name `market for | Cut-off, U`.
- `Soft wheat grain, at farm gate {FR} U` gives the product `Soft wheat grain, at farm gate` and
  the location `FR`, and no name.
- `Dyeing of cotton fabric` gives the product `Dyeing of cotton fabric` only.

- The product is the text before the braces, or the whole text when there are none.
- The location is the text inside the braces.
- The name is the text after the first `|` that follows the braces, system model included, so
  `... | Cut-off, U` and `... | Cut-off, S` stay two suppliers. A text without that part gives
  no name.

These are the supplier's name, product and location that a line gives for link by name.
"""

import re
from dataclasses import dataclass

_TEXT = re.compile(r"^(?P<product>.*?)\s*\{(?P<location>[^{}]+)\}(?P<rest>.*)$", re.DOTALL)


@dataclass(frozen=True)
class ProductText:
    name: str | None
    product: str
    location: str | None

    @classmethod
    def parse(cls, text: str) -> "ProductText":
        found = _TEXT.match(text.strip())
        if found is None or not found["product"].strip():
            return cls(None, text.strip(), None)
        rest = found["rest"].strip()
        name = rest[1:].strip() if rest.startswith("|") else None
        return cls(name or None, found["product"].strip(), found["location"].strip())
