"""Tests for ingestor transformation functions.

These are pure functions extracted from each ingestor module — no I/O,
no mocking needed. Every test supplies sample API-shaped input and
verifies the output records/columns.
"""

from __future__ import annotations

# ═══════════════════════════════════════════════════════════════════════════════
# Zabbix — _transform_hosts
# ═══════════════════════════════════════════════════════════════════════════════


class TestZabbixTransform:
    def test_transform_hosts_full_record(self) -> None:
        from zentinull.ingestors.zabbix import _transform_hosts

        items = [
            {
                "hostid": "10001",
                "host": "srv-web01",
                "name": "Web Server 01",
                "status": "0",
                "groups": [{"name": "Linux servers"}, {"name": "Production"}],
                "inventory": {
                    "os": "Ubuntu 22.04",
                    "type": "server",
                    "serial_no_a": "SN001",
                    "macaddress_a": "00:1a:2b:3c:4d:5e",
                    "location": "DC1-Rack3",
                },
                "interfaces": [{"ip": "10.0.1.10", "dns": "srv-web01.lan", "port": "10050", "type": "1"}],
                "tags": [{"tag": "role", "value": "web"}],
            }
        ]
        records, columns = _transform_hosts(items)

        assert len(records) == 1
        rec = records[0]
        assert rec["hostid"] == "10001"
        assert rec["hostname"] == "srv-web01"
        assert rec["name"] == "Web Server 01"
        assert rec["status"] == "0"
        assert rec["groups"] == "Linux servers, Production"
        assert rec["inventory_os"] == "Ubuntu 22.04"
        assert rec["inventory_type"] == "server"
        assert rec["inventory_serial"] == "SN001"
        assert rec["inventory_mac"] == "00:1a:2b:3c:4d:5e"
        assert rec["inventory_location"] == "DC1-Rack3"
        assert rec["ip_address"] == "10.0.1.10"
        assert "raw_json" in rec

        assert columns == [
            "hostid",
            "hostname",
            "name",
            "status",
            "groups",
            "inventory_os",
            "inventory_type",
            "inventory_serial",
            "inventory_mac",
            "inventory_location",
            "ip_address",
        ]

    def test_transform_hosts_missing_fields_default_to_empty(self) -> None:
        from zentinull.ingestors.zabbix import _transform_hosts

        items = [{"hostid": "1", "host": "test", "name": "test"}]
        records, _ = _transform_hosts(items)

        assert records[0]["groups"] == ""
        assert records[0]["inventory_os"] == ""
        assert records[0]["inventory_mac"] == ""
        assert records[0]["ip_address"] == ""

    def test_transform_hosts_empty_list(self) -> None:
        from zentinull.ingestors.zabbix import _transform_hosts

        records, columns = _transform_hosts([])
        assert records == []
        assert len(columns) > 0

    def test_transform_hosts_null_inventory(self) -> None:
        from zentinull.ingestors.zabbix import _transform_hosts

        items = [{"hostid": "1", "host": "a", "name": "b", "inventory": None}]
        records, _ = _transform_hosts(items)
        assert records[0]["inventory_os"] == ""

    def test_transform_hosts_no_interfaces(self) -> None:
        from zentinull.ingestors.zabbix import _transform_hosts

        items = [{"hostid": "1", "host": "a", "name": "b"}]
        records, _ = _transform_hosts(items)
        assert records[0]["ip_address"] == ""


# ═══════════════════════════════════════════════════════════════════════════════
# FortiGate — _transform_fg
# ═══════════════════════════════════════════════════════════════════════════════


class TestFortiGateTransform:
    def test_transform_fg_basic(self) -> None:
        from zentinull.ingestors.fortigate import _transform_fg

        items = [{"mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.1.10", "hostname": "client-01"}]
        cols = ["mac", "ip", "hostname"]
        records = _transform_fg(items, cols, "fg-host-01")

        assert len(records) == 1
        rec = records[0]
        assert rec["mac"] == "aa:bb:cc:dd:ee:ff"
        assert rec["ip"] == "192.168.1.10"
        assert rec["hostname"] == "client-01"
        assert rec["fg_host"] == "fg-host-01"
        assert "raw_json" in rec

    def test_transform_fg_missing_column_defaults_empty(self) -> None:
        from zentinull.ingestors.fortigate import _transform_fg

        items = [{"mac": "aa:bb:cc:dd:ee:ff"}]
        records = _transform_fg(items, ["mac", "ip", "hostname"], "fg")
        assert records[0]["ip"] == ""
        assert records[0]["hostname"] == ""

    def test_transform_fg_none_value_becomes_str(self) -> None:
        from zentinull.ingestors.fortigate import _transform_fg

        items = [{"mac": None, "ip": "10.0.0.1"}]
        records = _transform_fg(items, ["mac", "ip"], "fg")
        assert records[0]["mac"] == "None"  # str(None) is "None"

    def test_transform_fg_multiple_items(self) -> None:
        from zentinull.ingestors.fortigate import _transform_fg

        items = [{"mac": "a"}, {"mac": "b"}, {"mac": "c"}]
        records = _transform_fg(items, ["mac"], "fg")
        assert len(records) == 3

    def test_transform_fg_empty(self) -> None:
        from zentinull.ingestors.fortigate import _transform_fg

        records = _transform_fg([], ["mac"], "fg")
        assert records == []


# ═══════════════════════════════════════════════════════════════════════════════
# ManageEngine — _transform_ec_computers / _transform_mdm_devices
# ═══════════════════════════════════════════════════════════════════════════════


class TestManageEngineTransform:
    def test_transform_ec_computers_full(self) -> None:
        from zentinull.ingestors.manageengine import _transform_ec_computers

        items = [
            {
                "resource_id": 123,
                "serial_number": "SN-EC-001",
                "mac_address": "00:11:22:33:44:55",
                "name": "DESKTOP-ABC",
                "manufacturer": "Dell",
                "model": "OptiPlex 7090",
                "os_name": "Windows 10",
                "os_version": "10.0.19045",
                "logged_on_user": "jdoe",
                "last_scan_time": "2026-07-11T10:00:00Z",
                "domain_name": "MOONLITE",
                "ip_address": "192.168.10.50",
            }
        ]
        records, columns = _transform_ec_computers(items)

        assert len(records) == 1
        rec = records[0]
        assert rec["resource_id"] == "123"
        assert rec["serial_number"] == "SN-EC-001"
        assert rec["mac_address"] == "00:11:22:33:44:55"
        assert rec["name"] == "DESKTOP-ABC"
        assert rec["manufacturer"] == "Dell"
        assert rec["model"] == "OptiPlex 7090"
        assert rec["os_name"] == "Windows 10"
        assert rec["os_version"] == "10.0.19045"
        assert rec["assigned_user"] == "jdoe"
        assert rec["last_seen"] == "2026-07-11T10:00:00Z"
        assert rec["domain_name"] == "MOONLITE"
        assert rec["ip_address"] == "192.168.10.50"
        assert rec["source_type"] == "ec"
        assert "raw_json" in rec

        assert "resource_id" in columns
        assert "source_type" in columns
        assert "raw_json" not in columns  # added by create_table + insert_raw

    def test_transform_ec_computers_missing_fields(self) -> None:
        from zentinull.ingestors.manageengine import _transform_ec_computers

        items = [{"resource_id": 1}]
        records, _ = _transform_ec_computers(items)
        assert records[0]["serial_number"] == ""
        assert records[0]["source_type"] == "ec"

    def test_transform_ec_computers_empty(self) -> None:
        from zentinull.ingestors.manageengine import _transform_ec_computers

        records, columns = _transform_ec_computers([])
        assert records == []

    def test_transform_mdm_devices_full(self) -> None:
        from zentinull.ingestors.manageengine import _transform_mdm_devices

        items = [
            {
                "device_id": 456,
                "serial_number": "SN-MDM-002",
                "imei": "352656100123450",
                "udid": "ABCD-1234-EFGH-5678",
                "name": "iPhone 15",
                "model": "Apple iPhone 15 Pro",
                "os_version": "18.5",
                "user_email": "user@moonlite.local",
                "platform": "ios",
                "enrolled_time": "2026-06-01T08:00:00Z",
                "last_seen_time": "2026-07-11T09:00:00Z",
            }
        ]
        records, columns = _transform_mdm_devices(items)

        assert len(records) == 1
        rec = records[0]
        assert rec["device_id"] == "456"
        assert rec["serial_number"] == "SN-MDM-002"
        assert rec["imei"] == "352656100123450"
        assert rec["udid"] == "ABCD-1234-EFGH-5678"
        assert rec["name"] == "iPhone 15"
        assert rec["model"] == "Apple iPhone 15 Pro"
        assert rec["os_version"] == "18.5"
        assert rec["user_email"] == "user@moonlite.local"
        assert rec["platform"] == "ios"
        assert rec["enrolled_at"] == "2026-06-01T08:00:00Z"
        assert rec["last_seen"] == "2026-07-11T09:00:00Z"
        assert rec["source_type"] == "mdm"

        assert "source_type" in columns
        assert "raw_json" not in columns

    def test_transform_mdm_devices_missing_fields(self) -> None:
        from zentinull.ingestors.manageengine import _transform_mdm_devices

        items = [{"device_id": 1}]
        records, _ = _transform_mdm_devices(items)
        assert records[0]["serial_number"] == ""
        assert records[0]["source_type"] == "mdm"

    def test_transform_mdm_devices_empty(self) -> None:
        from zentinull.ingestors.manageengine import _transform_mdm_devices

        records, columns = _transform_mdm_devices([])
        assert records == []


# ═══════════════════════════════════════════════════════════════════════════════
# ServiceDesk Plus — _extract, _transform_tabular
# ═══════════════════════════════════════════════════════════════════════════════


class TestServiceDeskTransform:
    def test_extract_plain_value(self) -> None:
        from zentinull.ingestors.servicedeskplus import _extract

        assert _extract({"name": "Test Asset"}, "name") == "Test Asset"

    def test_extract_dict_value_uses_name(self) -> None:
        from zentinull.ingestors.servicedeskplus import _extract

        result = _extract({"technician": {"name": "John Doe", "id": 42}}, "technician")
        assert result == "John Doe"

    def test_extract_dict_without_name(self) -> None:
        from zentinull.ingestors.servicedeskplus import _extract

        result = _extract({"technician": {"email": "j@d.com"}}, "technician")
        assert result == "{'email': 'j@d.com'}"

    def test_extract_none_value(self) -> None:
        from zentinull.ingestors.servicedeskplus import _extract

        assert _extract({"status": None}, "status") == ""

    def test_extract_missing_key(self) -> None:
        from zentinull.ingestors.servicedeskplus import _extract

        assert _extract({"name": "x"}, "missing") == ""

    def test_transform_tabular_basic(self) -> None:
        from zentinull.ingestors.servicedeskplus import _transform_tabular

        items = [
            {
                "asset_id": 1001,
                "name": "Laptop-01",
                "serial_number": "SN-SDP-001",
                "status": {"name": "In Use"},
            }
        ]
        cols = ["asset_id", "name", "serial_number", "status"]
        records = _transform_tabular(items, cols)

        assert len(records) == 1
        rec = records[0]
        assert rec["asset_id"] == "1001"
        assert rec["name"] == "Laptop-01"
        assert rec["serial_number"] == "SN-SDP-001"
        assert rec["status"] == "In Use"
        assert "raw_json" in rec

    def test_transform_tabular_empty(self) -> None:
        from zentinull.ingestors.servicedeskplus import _transform_tabular

        assert _transform_tabular([], ["col1"]) == []

    def test_transform_tabular_multiple_items(self) -> None:
        from zentinull.ingestors.servicedeskplus import _transform_tabular

        items = [{"name": "A"}, {"name": "B"}, {"name": "C"}]
        records = _transform_tabular(items, ["name"])
        assert len(records) == 3


# ═══════════════════════════════════════════════════════════════════════════════
# SharePoint — _sanitize_col_name, _transform_sharepoint
# ═══════════════════════════════════════════════════════════════════════════════


class TestSharePointTransform:
    def test_sanitize_col_name_basic(self) -> None:
        from zentinull.ingestors.sharepoint import _sanitize_col_name

        existing: set[str] = set()
        assert _sanitize_col_name("DisplayName", existing) == "displayname"
        assert _sanitize_col_name("IP Address", existing) == "ip_address"

    def test_sanitize_col_name_at_symbol(self) -> None:
        from zentinull.ingestors.sharepoint import _sanitize_col_name

        existing: set[str] = set()
        assert _sanitize_col_name("user@domain", existing) == "userdomain"

    def test_sanitize_col_name_id_becomes_sp_id(self) -> None:
        from zentinull.ingestors.sharepoint import _sanitize_col_name

        existing: set[str] = set()
        assert _sanitize_col_name("id", existing) == "sp_id"

    def test_sanitize_col_name_empty_becomes_col(self) -> None:
        from zentinull.ingestors.sharepoint import _sanitize_col_name

        existing: set[str] = set()
        result = _sanitize_col_name("___", existing)
        assert result == "col" or result.startswith("col")

    def test_sanitize_col_name_deduplicates(self) -> None:
        from zentinull.ingestors.sharepoint import _sanitize_col_name

        existing: set[str] = {"name"}
        result = _sanitize_col_name("name", existing)
        assert result == "name_"

    def test_transform_sharepoint_simple(self) -> None:
        from zentinull.ingestors.sharepoint import _transform_sharepoint

        items = [
            {"fields": {"Title": "Device A", "Manufacturer": "Dell"}, "id": 101},
            {"fields": {"Title": "Device B", "Manufacturer": "HP"}, "id": 102},
        ]
        records, columns = _transform_sharepoint(items)

        assert len(records) == 2
        assert records[0]["title"] == "Device A"
        assert records[0]["manufacturer"] == "Dell"
        assert records[0]["sharepoint_id"] == "101"
        assert records[1]["title"] == "Device B"
        assert records[1]["manufacturer"] == "HP"
        assert records[1]["sharepoint_id"] == "102"

        assert "title" in columns
        assert "manufacturer" in columns
        assert "sharepoint_id" in columns

    def test_transform_sharepoint_direct_item(self) -> None:
        from zentinull.ingestors.sharepoint import _transform_sharepoint

        items = [{"name": "Direct Item", "value": 42}]
        records, _ = _transform_sharepoint(items)
        assert records[0]["name"] == "Direct Item"
        assert records[0]["value"] == "42"

    def test_transform_sharepoint_filters_dicts_and_lists(self) -> None:
        from zentinull.ingestors.sharepoint import _transform_sharepoint

        items = [{"fields": {"simple": "ok", "nested": {"x": 1}, "arr": [1, 2]}}]
        records, _ = _transform_sharepoint(items)
        assert records[0]["simple"] == "ok"
        assert "nested" not in records[0]
        assert "arr" not in records[0]

    def test_transform_sharepoint_empty(self) -> None:
        from zentinull.ingestors.sharepoint import _transform_sharepoint

        records, columns = _transform_sharepoint([])
        assert records == []
        assert columns == []

    def test_transform_sharepoint_none_value_becomes_str(self) -> None:
        from zentinull.ingestors.sharepoint import _transform_sharepoint

        items = [{"fields": {"title": None, "other": "val"}}]
        records, _ = _transform_sharepoint(items)
        assert records[0]["title"] == ""
        assert records[0]["other"] == "val"


# ═══════════════════════════════════════════════════════════════════════════════
# AD — _safe_attr, _transform_ad
# ═══════════════════════════════════════════════════════════════════════════════


class TestADTransform:
    def test_safe_attr_first_value(self) -> None:
        from zentinull.ingestors.ad import _safe_attr

        attrs = {"sAMAccountName": ["PC-001"], "operatingSystem": ["Windows 10"]}
        assert _safe_attr(attrs, "sAMAccountName") == "PC-001"
        assert _safe_attr(attrs, "operatingSystem") == "Windows 10"

    def test_safe_attr_missing_key(self) -> None:
        from zentinull.ingestors.ad import _safe_attr

        assert _safe_attr({}, "nonexistent") == ""

    def test_safe_attr_empty_list(self) -> None:
        from zentinull.ingestors.ad import _safe_attr

        assert _safe_attr({"key": []}, "key") == ""

    def test_safe_attr_custom_idx_and_default(self) -> None:
        from zentinull.ingestors.ad import _safe_attr

        attrs = {"multi": ["a", "b", "c"]}
        assert _safe_attr(attrs, "multi", idx=1) == "b"
        assert _safe_attr(attrs, "multi", idx=5) == ""
        assert _safe_attr(attrs, "none", default="N/A") == "N/A"

    def test_transform_ad_full(self) -> None:
        from zentinull.ingestors.ad import _transform_ad

        attrs_list = [
            {
                "sAMAccountName": ["WS-001"],
                "dNSHostName": ["ws-001.moonlite.local"],
                "operatingSystem": ["Windows 11"],
                "operatingSystemVersion": ["10.0.22631"],
                "description": ["User workstation"],
                "location": ["Floor 2"],
                "whenCreated": ["2026-01-15 10:00:00"],
                "lastLogonTimestamp": ["133500000000000000"],
                "whenChanged": ["2026-07-10 15:30:00"],
                "userAccountControl": ["512"],
                "managedBy": ["CN=Admin,OU=Users,DC=moonlite,DC=local"],
            }
        ]
        dns = ["CN=WS-001,OU=Computers,DC=moonlite,DC=local"]
        records, columns = _transform_ad(attrs_list, dns)

        assert len(records) == 1
        rec = records[0]
        assert rec["sam_account_name"] == "WS-001"
        assert rec["dns_host_name"] == "ws-001.moonlite.local"
        assert rec["operating_system"] == "Windows 11"
        assert rec["os_version"] == "10.0.22631"
        assert rec["distinguished_name"] == "CN=WS-001,OU=Computers,DC=moonlite,DC=local"
        assert rec["description"] == "User workstation"
        assert rec["location"] == "Floor 2"
        assert rec["created"] == "2026-01-15 10:00:00"
        assert rec["last_logon"] == "133500000000000000"
        assert rec["when_changed"] == "2026-07-10 15:30:00"
        assert rec["user_account_control"] == "512"
        assert rec["managed_by"] == "CN=Admin,OU=Users,DC=moonlite,DC=local"
        assert "raw_json" in rec

        assert "sam_account_name" in columns
        assert "distinguished_name" in columns

    def test_transform_ad_missing_attrs(self) -> None:
        from zentinull.ingestors.ad import _transform_ad

        attrs_list = [{"sAMAccountName": ["WS-002"]}]
        dns = ["CN=WS-002,DC=moonlite,DC=local"]
        records, _ = _transform_ad(attrs_list, dns)

        rec = records[0]
        assert rec["dns_host_name"] == ""
        assert rec["operating_system"] == ""
        assert rec["description"] == ""

    def test_transform_ad_empty(self) -> None:
        from zentinull.ingestors.ad import _transform_ad

        records, _ = _transform_ad([], [])
        assert records == []
