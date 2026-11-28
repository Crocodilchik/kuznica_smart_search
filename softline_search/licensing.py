"""Quantity of sale units needed for one offer under the catalog's purchase terms."""


def units_to_buy(product, devices: int | None) -> tuple[int, str | None]:
    minimum = product.min_purchase or 1
    if devices is None:
        return minimum, None
    if not product.license_units or product.license_unit_type != "device":
        return 0, "license_capacity_unknown"
    packs = (devices + product.license_units - 1) // product.license_units
    if packs > 1 and product.attributes.get("license_stackable") is not True:
        return 0, "license_capacity_insufficient"
    return max(minimum, packs), None
