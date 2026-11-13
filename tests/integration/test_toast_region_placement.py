"""The toast region must sit outside ``#mainContent``.

Soft navigation replaces the children of ``#mainContent``. When the region was
rendered after the page content, it was inside that element and every visible
toast was removed on navigation (reproduced in a headless browser). The region
is ``position: fixed``, so placing it before ``#mainContent`` does not move it.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("path", ["/", "/files"])
def test_the_toast_region_is_outside_the_swapped_content(app, admin_client, path):
    resp = admin_client.get(path, follow_redirects=True)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    region = html.find('id="toastRegion"')
    main = html.find('id="mainContent"')
    assert region != -1, "toast region missing from the page"
    assert main != -1
    assert region < main, "toast region is inside #mainContent; soft navigation would remove its toasts"
    assert html.count("data-toast-region") == 1
