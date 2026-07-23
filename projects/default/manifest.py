"""Default project manifest — 6 systems, 23 feeds.

This manifest defines the Zentinull device entity resolution pipeline:
- 8 ANCHOR feeds (device records from each source)
- 6 ATTACHMENT feeds (zbx items, sdp requests, sp_employees, sp_accountinfo,
  sp_devicenotes, sp_componentpurchases)
- 9 CONTEXT feeds (supplementary data)
- 1 ResolutionProfile (device) with Splink config
"""

from __future__ import annotations

from zentinull.config import (
    SPLINK_LAMBDA_RECALL,
    SPLINK_PREDICT_THRESHOLD,
    SPLINK_SWEEP_THRESHOLDS,
    SPLINK_THRESHOLD,
    SPLINK_U_MAX_PAIRS,
)
from zentinull.manifest.types import (
    Auth,
    Comparison,
    Feed,
    FieldSpec,
    Link,
    Manifest,
    ResolutionProfile,
    Role,
    System,
)

# ── Systems ──────────────────────────────────────────────────────────────────

SYSTEMS = {
    "sp": System(
        auth=Auth(kind="none"),
        strategy="rest_json",
        label="SharePoint",
        schedule=43200,  # 12h
        coverage=0.55,
        fields=("name", "mfr", "model", "serial", "os", "user", "ip"),
    ),
    "me": System(
        auth=Auth(kind="oauth_refresh", options={"client_id": "ME_CLIENT_ID", "client_secret": "ME_CLIENT_SECRET"}),
        strategy="paged_json_detail",
        label="ManageEngine",
        schedule=7200,  # 2h
        coverage=0.50,
        fields=("name", "mfr", "model", "serial", "os", "user"),
    ),
    "fg": System(
        auth=Auth(kind="api_key", options={"api_key": "FG_API_KEY"}),
        strategy="rest_json",
        label="FortiGate",
        schedule=1800,  # 30min
        coverage=0.40,
        fields=("name", "os", "ip", "mac"),
    ),
    "zbx": System(
        auth=Auth(kind="api_key", options={"api_key": "ZBX_TOKEN"}),
        strategy="json_rpc",
        label="Zabbix",
        schedule=600,  # 10min
        coverage=0.45,
        fields=("name", "ip", "os"),
    ),
    "ad": System(
        auth=Auth(kind="ldap", options={"server": "AD_SERVER", "user": "AD_USER", "password": "AD_PASSWORD"}),
        strategy="ldap",
        label="Active Directory",
        schedule=21600,  # 6h
        coverage=0.60,
        fields=("name", "serial", "os", "user"),
    ),
    "sdp": System(
        auth=Auth(kind="oauth_refresh", options={"client_id": "SDP_CLIENT_ID", "client_secret": "SDP_CLIENT_SECRET"}),
        strategy="sdp_cursor",
        label="ServiceDesk Plus",
        schedule=7200,  # 2h
        coverage=0.30,
        fields=("name", "mfr", "model", "serial", "os", "user", "mac", "imei"),
    ),
}

# ── Resolution Profile ───────────────────────────────────────────────────────

DEVICE_PROFILE = ResolutionProfile(
    name="device",
    fields=(
        "source",
        "source_id",
        "name",
        "name_clean",
        "serial_number",
        "mac_address",
        "mac_clean",
        "name_fallback",
        "asset_tag",
        "manufacturer",
        "model",
        "os",
        "os_version",
        "assigned_user",
        "ip_address",
        "imei",
        "mdm_latitude",
        "mdm_longitude",
        "mdm_horizontal_accuracy",
        "mdm_location_address",
        "mdm_located_time",
        "extra_attributes",
        "os_family",
    ),
    derived={
        "name_clean": ("name", "name"),
        "mac_clean": ("mac_address", "mac"),
        "name_fallback": ("name", "name_fallback"),
        "os_family": ("os", "os_family"),
    },
    comparisons=(
        Comparison(kind="levenshtein", column="serial_number", thresholds=(1.0, 2.0)),
        Comparison(kind="levenshtein", column="mac_clean", thresholds=(1.0, 2.0)),
        Comparison(kind="exact", column="name_clean"),
        Comparison(kind="exact", column="ip_address", term_frequency_adjustments=True),
        Comparison(kind="exact", column="manufacturer", term_frequency_adjustments=True),
        Comparison(kind="exact", column="assigned_user", term_frequency_adjustments=True),
        Comparison(kind="exact", column="os_family", term_frequency_adjustments=True),
    ),
    blocking=("serial_number", "mac_clean", "name_clean", "name_fallback"),
    deterministic=("serial_number", "mac_clean", "name_clean", "name_fallback"),
    em_passes=("serial_number", "mac_clean", "name_clean", "name_fallback"),
    predict_threshold=SPLINK_PREDICT_THRESHOLD,
    cluster_threshold=float(SPLINK_THRESHOLD),
    sweep_thresholds=tuple(float(t) for t in SPLINK_SWEEP_THRESHOLDS),
    u_max_pairs=SPLINK_U_MAX_PAIRS,
    lambda_recall=SPLINK_LAMBDA_RECALL,
    sot={
        "name": ("sp", ""),
        "serial_number": ("me", "sp"),
        "mac_address": ("fg", "me"),
        "ip_address": ("zbx", "fg"),
        "manufacturer": ("me", "sp"),
        "model": ("me", "sp"),
        "os": ("me", "sp"),
        "os_family": ("me", "sp"),
        "os_version": ("me", "sdp"),
        "assigned_user": ("sp", "me"),
        "imei": ("sdp", "me"),
        "asset_tag": ("sp", "sdp"),
    },
)

PROFILES = {"device": DEVICE_PROFILE}

# ── Feeds ────────────────────────────────────────────────────────────────────

FEEDS = {
    # ANCHOR feeds (8) — device records from each source
    "sp_devices": Feed(
        system="sp",
        endpoint={"base": "SHAREPOINT_BASE_URL", "path": "/sp_devices"},
        role=Role.ANCHOR,
        profile="device",
        store="sp_devices",
        id_path="id",
        spec={
            "source_id": ("id",),
            "name": ("fields.Title",),
            "serial_number": FieldSpec(paths=("fields.SerialNumber",), transform="serial"),
            "mac_address": ("fields.ETHMAC", "fields.WLANMAC"),
            "asset_tag": ("fields.AssetNumber",),
            "manufacturer": FieldSpec(paths=("fields.ManufacturerString",), transform="lower"),
            "model": ("fields.Model",),
            "assigned_user": ("fields.AssignedUserString",),
            "os": ("fields.OperatingSystem",),
        },
    ),
    "me_ec": Feed(
        system="me",
        endpoint={
            "base": "ME_CLOUD_BASE_URL",
            "path": "/inventory/scancomputers",
            "response_path": "message_response.scancomputers",
            "pagination": "page_param",
            "detail_url_template": "{base}/inventory/details?resource_id={id}",
            "detail_id_field": "resource_id",
            "detail_delay": 0.15,
        },
        role=Role.ANCHOR,
        profile="device",
        store="computers",
        id_path="resource_id",
        spec={
            "source_id": ("resource_id", "RESOURCEID"),
            "name": ("fqdn_name", "resource_name", "NAME", "name"),
            "serial_number": FieldSpec(paths=("servicetag", "serial_number", "SERIALNUMBER"), transform="serial"),
            "mac_address": ("mac_address", "MACADDRESS"),
            "manufacturer": ("hardware_vendor", "VENDOR", "manufacturer"),
            "model": ("model", "MODEL"),
            "os": ("os_name", "OSNAME"),
            "os_version": ("os_version", "OSVERSION"),
            "assigned_user": ("agent_logged_on_users", "USERNAME", "assigned_user"),
            "ip_address": ("ip_address", "IPADDRESS"),
        },
    ),
    "me_mdm": Feed(
        system="me",
        endpoint={
            "base": "ME_MDM_BASE_URL",
            "path": "/devices",
            "pagination": "paging.next",
            "detail_url_template": "{base}/devices/{id}",
            "detail_id_field": "device_id",
            "detail_delay": 0.3,
            "secondary_detail_url_template": "{base}/devices/{id}/locations",
            "secondary_response_key": "locations",
            "secondary_fields": {
                "mdm_latitude": "latitude",
                "mdm_longitude": "longitude",
                "mdm_horizontal_accuracy": "horizontal_accuracy",
                "mdm_location_address": "address",
                "mdm_located_time": "added_time",
            },
        },
        role=Role.ANCHOR,
        profile="device",
        store="mdm_devices",
        id_path="device_id",
        updated_path="last_contact_time",
        spec={
            "source_id": ("device_id", "DEVICEID"),
            "name": ("device_name", "NAME", "name"),
            "serial_number": ("serial_number", "SERIALNUMBER"),
            "mac_address": ("wifi_mac", "MACADDRESS", "mac_address"),
            "manufacturer": ("product_name", "MANUFACTURER", "manufacturer"),
            "model": ("model", "MODEL"),
            "os": ("platform_type", "OS", "platform"),
            "os_version": ("os_version", "OSVERSION"),
            "assigned_user": ("user.user_email", "USEREMAIL", "user_email"),
            "imei": FieldSpec(paths=("imei",), transform="first_of_list"),
            "mdm_latitude": ("mdm_latitude", "latitude"),
            "mdm_longitude": ("mdm_longitude", "longitude"),
            "mdm_horizontal_accuracy": ("mdm_horizontal_accuracy", "horizontal_accuracy"),
            "mdm_location_address": ("mdm_location_address", "address"),
            "mdm_located_time": ("mdm_located_time", "added_time"),
        },
    ),
    "fg_clients": Feed(
        system="fg",
        endpoint={"base": "FG_BASE_URL", "path": "/api/v2/monitor/user/device/query", "response_path": "results"},
        role=Role.ANCHOR,
        profile="device",
        store="clients",
        id_path="mac",
        spec={
            "source_id": ("mac",),
            "name": ("hostname",),
            "mac_address": ("mac",),
            "ip_address": ("ipv4_address", "ip"),
            "manufacturer": ("hardware_vendor", "manufacturer"),
            "model": ("hardware_family", "hardware_type", "model"),
            "os": ("os_name", "os"),
            "os_version": ("os_version",),
            "assigned_user": ("unauth_user", "user_name"),
        },
    ),
    "fg_dhcp": Feed(
        system="fg",
        endpoint={"base": "FG_BASE_URL", "path": "/api/v2/monitor/system/dhcp", "response_path": "results"},
        role=Role.ANCHOR,
        profile="device",
        store="dhcp_leases",
        id_path="mac",
        spec={
            "source_id": ("mac",),
            "name": ("hostname",),
            "mac_address": ("mac",),
            "ip_address": ("ip",),
        },
    ),
    "zbx_hosts": Feed(
        system="zbx",
        endpoint={
            "base": "ZBX_URL",
            "method": "host.get",
            "params": {
                "output": ["hostid", "host", "name", "status", "description"],
                "selectGroups": ["name"],
                "selectInventory": [
                    "os",
                    "os_short",
                    "os_full",
                    "type",
                    "type_full",
                    "serial_no_a",
                    "serial_no_b",
                    "macaddress_a",
                    "macaddress_b",
                    "tag",
                    "location",
                ],
                "selectInterfaces": ["ip", "dns", "port", "type"],
                "selectTags": ["tag", "value"],
            },
        },
        role=Role.ANCHOR,
        profile="device",
        store="hosts",
        id_path="hostid",
        spec={
            "source_id": ("hostid",),
            "name": FieldSpec(paths=("host", "name")),
            "ip_address": ("interfaces.0.ip", "ip"),
            "os": ("inventory.os", "inventory.os_full"),
            "os_version": ("inventory.os_short",),
            "serial_number": FieldSpec(
                paths=("inventory.serial_no_a", "inventory.serial_no_b"),
                transform="serial",
            ),
            "mac_address": FieldSpec(
                paths=("inventory.macaddress_a", "inventory.macaddress_b"),
                transform="mac",
            ),
            "model": ("inventory.type", "inventory.type_full"),
            "asset_tag": ("inventory.tag",),
        },
    ),
    "ad_computers": Feed(
        system="ad",
        endpoint={
            "search_base_conf": "AD_SEARCH_BASE",
            "search_filter": "(objectClass=computer)",
            "attributes": [
                "sAMAccountName",
                "dNSHostName",
                "operatingSystem",
                "operatingSystemVersion",
                "distinguishedName",
                "lastLogonTimestamp",
                "whenCreated",
                "whenChanged",
                "description",
                "location",
                "userAccountControl",
                "managedBy",
                "serialNumber",
            ],
            "size_limit": 5000,
        },
        role=Role.ANCHOR,
        profile="device",
        store="computers",
        id_path="sAMAccountName",
        spec={
            "source_id": ("sAMAccountName", "dNSHostName"),
            "name": ("dNSHostName",),
            "serial_number": FieldSpec(paths=("serialNumber",), transform="serial"),
            "manufacturer": ("manufacturer",),
            "model": ("model",),
            "os": ("operatingSystem",),
            "os_version": ("operatingSystemVersion",),
            "assigned_user": ("managedBy",),
        },
    ),
    "sdp_assets": Feed(
        system="sdp",
        endpoint={
            "base": "SDP_BASE_URL",
            "path": "/api/v3/assets",
            "response_path": "assets",
            "pagination": {"row_count": 100, "sort_field": "id", "sort_order": "asc"},
            "field_names": [
                "id", "name", "product", "manufacturer", "model",
                "serial_number", "mac_address", "asset_tag", "os",
                "assigned_user", "imei", "barcode", "location",
            ],
        },
        role=Role.ANCHOR,
        profile="device",
        store="assets",
        id_path="id",
        spec={
            "source_id": ("id",),
            "name": ("name",),
            "serial_number": FieldSpec(paths=("serial_number",), transform="serial"),
            "mac_address": ("mac_address",),
            "asset_tag": ("barcode", "asset_tag"),
            "manufacturer": ("product.manufacturer", "manufacturer"),
            "model": ("product.name", "model"),
            "os": ("os",),
            "assigned_user": ("created_by.name", "assigned_user"),
        },
    ),
    # ATTACHMENT feeds (2) — link to anchors, never merge
    "zbx_items": Feed(
        system="zbx",
        endpoint={
            "base": "ZBX_URL",
            "method": "item.get",
            "params": {
                "output": [
                    "itemid",
                    "hostid",
                    "name",
                    "key_",
                    "value_type",
                    "units",
                    "lastvalue",
                    "lastclock",
                    "prevvalue",
                ],
            },
            "timeout": (10, 90),
        },
        role=Role.ATTACHMENT,
        store="items",
        id_path="itemid",
        links=(Link(field="hostid", to="device", on="source_id", scope=("zbx_hosts",)),),
        spec={
            "name": ("name",),
            "value": ("lastvalue",),
        },
    ),
    "sdp_requests": Feed(
        system="sdp",
        endpoint={
            "base": "SDP_BASE_URL",
            "path": "/api/v3/requests",
            "response_path": "requests",
            "pagination": {"row_count": 100, "sort_field": "id", "sort_order": "desc"},
        },
        role=Role.ATTACHMENT,
        store="requests",
        id_path="id",
        links=(Link(field="subject", to="device", on="name", strategy="extract_fuzzy", multi=True),),
        spec={
            "subject": ("subject",),
            "status": ("status.name",),
        },
    ),
    # ATTACHMENT feeds (6) — linked to device clusters after resolution
    "sp_employees": Feed(
        system="sp",
        endpoint={"base": "SHAREPOINT_BASE_URL", "path": "/sp_employees"},
        role=Role.ATTACHMENT,
        store="sp_employees",
        id_path="id",
        links=(
            Link(
                field="fields.BusEmailAddress",
                to="device",
                on="assigned_user",
                strategy="exact",
                scope=("sp_devices", "me_ec", "me_mdm", "fg_clients", "ad_computers"),
            ),
            Link(
                field="fields.ReadName",
                to="device",
                on="assigned_user",
                strategy="exact",
                scope=("sp_devices", "me_ec", "me_mdm", "fg_clients", "ad_computers"),
            ),
            Link(
                field="fields.MLUsername",
                to="device",
                on="assigned_user",
                strategy="exact",
                scope=("sp_devices", "me_ec", "me_mdm", "fg_clients", "ad_computers"),
            ),
        ),
    ),
    "sp_accountinfo": Feed(
        system="sp",
        endpoint={"base": "SHAREPOINT_BASE_URL", "path": "/sp_AccountInfo"},
        role=Role.ATTACHMENT,
        store="sp_AccountInfo",
        id_path="id",
        links=(
            Link(
                field="fields.DeviceString",
                to="device",
                on="name_clean",
                strategy="exact",
                scope=("sp_devices",),
            ),
            Link(
                field="fields.EmployeeString",
                to="device",
                on="assigned_user",
                strategy="exact",
                scope=("sp_devices", "me_ec", "me_mdm", "fg_clients", "ad_computers"),
            ),
        ),
    ),
    "sp_devicenotes": Feed(
        system="sp",
        endpoint={"base": "SHAREPOINT_BASE_URL", "path": "/sp_devicenotes"},
        role=Role.ATTACHMENT,
        store="sp_devicenotes",
        id_path="id",
        links=(
            Link(
                field="fields.LookupToDevicesLookupId",
                to="device",
                on="source_id",
                strategy="exact",
                scope=("sp_devices",),
            ),
        ),
    ),
    "sp_employeedocs": Feed(
        system="sp",
        # n8n webhook at /webhook/sp_employeedocs — returns a flat JSON array
        # of SharePoint agreement file records. Same host as the rest of the
        # SP feeds so we reuse N8N_BASE_URL.
        endpoint={"base": "N8N_BASE_URL", "path": "/sp_employeedocs"},
        role=Role.ATTACHMENT,
        store="sp_employeedocs",
        id_path="fields.ID",
        links=(
            # Employee name lives in the URL path, e.g.
            # /Agreements/Rick%20Ahmed__10/foo.pdf → "Rick Ahmed".
            # The transform decodes + extracts; the result is looked up
            # against `assigned_user` in the keyspace — same value SP devices
            # and other anchors already use, so docs link to the same
            # clusters the employee already attaches to.
            Link(
                field="webUrl",
                to="device",
                on="assigned_user",
                strategy="normalized",
                transform="employee_name_from_url",
                multi=True,
                scope=("sp_devices", "me_ec", "me_mdm", "fg_clients", "ad_computers"),
            ),
        ),
    ),
    "sp_componentpurchases": Feed(
        system="sp",
        endpoint={"base": "SHAREPOINT_BASE_URL", "path": "/sp_ComponentPurchases"},
        role=Role.ATTACHMENT,
        store="sp_ComponentPurchases",
        id_path="id",
        links=(
            Link(
                field="fields.LookupToDevicesLookupId",
                to="device",
                on="source_id",
                strategy="exact",
                scope=("sp_devices",),
            ),
        ),
    ),
    # CONTEXT feeds (9) — stored but not resolved
    "sp_vlans": Feed(
        system="sp",
        endpoint={"base": "SHAREPOINT_BASE_URL", "path": "/sp_vlans"},
        role=Role.CONTEXT,
        store="sp_vlans",
        id_path="id",
    ),
    "sdp_contracts": Feed(
        system="sdp",
        endpoint={
            "base": "SDP_BASE_URL",
            "path": "/api/v3/contracts",
            "response_path": "contracts",
            "pagination": {"row_count": 50, "sort_field": "id", "sort_order": "asc"},
        },
        role=Role.CONTEXT,
        store="contracts",
        id_path="id",
    ),
    "sdp_purchase_orders": Feed(
        system="sdp",
        endpoint={
            "base": "SDP_BASE_URL",
            "path": "/api/v3/purchase_orders",
            "response_path": "purchase_orders",
            "pagination": {"row_count": 50, "sort_field": "id", "sort_order": "asc"},
        },
        role=Role.CONTEXT,
        store="purchase_orders",
        id_path="id",
    ),
    "fg_resource_usage": Feed(
        system="fg",
        endpoint={"base": "FG_BASE_URL", "path": "/api/v2/monitor/system/resource/usage", "response_path": "results"},
        role=Role.CONTEXT,
        store="resource_usage",
        id_path="id",
    ),
    "fg_vpn_sessions": Feed(
        system="fg",
        endpoint={"base": "FG_BASE_URL", "path": "/api/v2/monitor/vpn/ssl", "response_path": "results"},
        role=Role.CONTEXT,
        store="vpn_sessions",
        id_path="id",
    ),
    "fg_known_devices": Feed(
        system="fg",
        endpoint={"base": "FG_BASE_URL", "path": "/api/v2/monitor/user/device", "response_path": "results"},
        role=Role.CONTEXT,
        store="known_devices",
        id_path="mac",
    ),
    "fg_firewall_policies": Feed(
        system="fg",
        endpoint={"base": "FG_BASE_URL", "path": "/api/v2/monitor/firewall/policy", "response_path": "results"},
        role=Role.CONTEXT,
        store="firewall_policies",
        id_path="policyid",
    ),
    "fg_interfaces": Feed(
        system="fg",
        endpoint={"base": "FG_BASE_URL", "path": "/api/v2/monitor/system/interface", "response_path": "results"},
        role=Role.CONTEXT,
        store="interfaces",
        id_path="id",
    ),
    "fg_arp_table": Feed(
        system="fg",
        endpoint={"base": "FG_BASE_URL", "path": "/api/v2/monitor/system/arp-table", "response_path": "results"},
        role=Role.CONTEXT,
        store="arp_table",
        id_path="mac",
    ),
}

# ── Manifest ─────────────────────────────────────────────────────────────────

MANIFEST = Manifest(
    project="default",
    systems=SYSTEMS,
    feeds=FEEDS,
    profiles=PROFILES,
)
