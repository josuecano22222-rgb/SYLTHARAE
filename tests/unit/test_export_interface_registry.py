"""Export surface registry: every UI-mapped interface has a backend spec."""

from __future__ import annotations

from Api.routes.exports import EXPORT_INTERFACE_BY_ENDPOINT, EXPORT_SPECS


def test_export_interface_by_endpoint_maps_to_specs():
    missing = [
        endpoint
        for endpoint, interface_id in EXPORT_INTERFACE_BY_ENDPOINT.items()
        if interface_id not in EXPORT_SPECS
    ]
    assert not missing, f"Endpoints without EXPORT_SPECS: {missing}"


def test_every_export_spec_has_columns_and_rows():
    for interface_id, spec in EXPORT_SPECS.items():
        assert spec.get("columns"), interface_id
        assert callable(spec.get("rows")), interface_id
