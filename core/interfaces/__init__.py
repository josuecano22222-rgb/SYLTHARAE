"""The SYLTHARAE interface registry.

One description of what the product offers, in one place, for one reason: the
application used to answer "do I know about this page?" with a dictionary that
lived inside the settings adapter and drifted from what was actually served.
Now the question is "what interface does this capability belong to, what domain
owns it, what does it need, and what does the registry say about it?".

This package is description only - see ``docs/INTERFACE_REGISTRY.md``:
authorisation stays in ``core/security``, values stay in the settings engine,
URLs stay in Flask's URL map.
"""

from .domains import (DOMAIN_DESCRIPTIONS, DOMAIN_LABELS, DOMAIN_ORDER, Domain,
                      domain_label, domain_values)
from .inventory import (
    SYSTEM_ENDPOINTS,
    EndpointClass,
    EndpointRecord,
    build_inventory,
    classify_endpoint,
    counts,
    inventory_json,
    is_infrastructure_endpoint,
    registry_coverage,
    unowned_user_interfaces,
)
from .model import (
    Feature,
    Interface,
    InterfaceKind,
    InterfaceStatus,
    LIVE_STATUSES,
)
from .registry import (
    FEATURES,
    LEGACY_INTERFACE_IDS,
    REGISTRY,
    RETIRED_INTERFACE_IDS,
    default_enabled,
    defaults,
    endpoints_to_interfaces,
    get_all_interfaces,
    get_dependencies,
    get_dependents,
    get_feature,
    get_features,
    get_interface,
    get_interface_for_endpoint,
    get_interfaces_by_domain,
    interface_ids,
    is_registered,
    iter_endpoints,
    navigable_interfaces,
    resolve_interface_id,
    summary,
)
from .lifecycle import (
    StatusPolicy,
    all_policies,
    policy as status_policy,
)
from .navigation import (
    Crumb,
    InterfaceConditions,
    NavigationEntry,
    NavigationGroup,
    PagePresentation,
    breadcrumbs_for,
    build_navigation,
    interface_conditions,
    navigation_model,
    present_page,
)
from .validation import Issue, errors_only, format_issues, validate_registry

__all__ = [
    # domains
    "Domain", "DOMAIN_ORDER", "DOMAIN_DESCRIPTIONS", "DOMAIN_LABELS",
    "domain_values", "domain_label",
    # model
    "Interface", "Feature", "InterfaceKind", "InterfaceStatus", "LIVE_STATUSES",
    # registry
    "REGISTRY", "FEATURES", "LEGACY_INTERFACE_IDS", "RETIRED_INTERFACE_IDS",
    "get_all_interfaces", "get_interface", "is_registered", "get_feature",
    "get_features", "get_interfaces_by_domain", "resolve_interface_id",
    "get_interface_for_endpoint", "endpoints_to_interfaces", "get_dependencies",
    "get_dependents", "default_enabled", "defaults", "interface_ids",
    "navigable_interfaces", "summary", "iter_endpoints",
    # inventory
    "EndpointClass", "EndpointRecord", "build_inventory", "classify_endpoint",
    "counts", "registry_coverage", "unowned_user_interfaces", "inventory_json",
    "SYSTEM_ENDPOINTS", "is_infrastructure_endpoint",
    # lifecycle
    "StatusPolicy", "all_policies", "status_policy",
    # navigation and page identity
    "NavigationEntry", "NavigationGroup", "Crumb", "PagePresentation",
    "InterfaceConditions", "build_navigation", "present_page",
    "breadcrumbs_for", "interface_conditions", "navigation_model",
    # validation
    "Issue", "validate_registry", "errors_only", "format_issues",
]
