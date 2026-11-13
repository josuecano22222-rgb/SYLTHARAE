"""The archive overview must populate every section before rendering."""

import logging


def test_archive_overview_loads_all_sections_before_reporting_their_counts(
    admin_client, caplog
):
    """A logging regression must not silently turn a real page into an empty one."""
    caplog.set_level(logging.INFO, logger="Api.routes.archives")

    response = admin_client.get("/archives")

    assert response.status_code == 200
    messages = [record.getMessage() for record in caplog.records
                if record.name == "Api.routes.archives"]
    assert any("Archives navigation lists capped" in message for message in messages)
    assert not any("Error loading archives page" in message for message in messages)
