# Field Mapping Audit Report
**Date:** 2026-07-14  
**Scope:** Zentinull manifest-driven data pipeline  
**Focus:** Field extraction quality, path mismatches, and export coverage

---

## Executive Summary

The field mapping audit reveals **3 critical path mismatches**, **2 data quality issues**, and **significant coverage gaps** across multiple feeds. While the manifest walker correctly handles list-to-string conversion and sentinel stripping, several feeds have manifest paths that don't match actual API response structures, resulting in 0% extraction rates for key fields.

**Key Findings:**
- 3 feeds have manifest paths that don't exist in API responses (zbx_hosts serial/mac, me_ec manufacturer/model, sp_devices assigned_user "null" string)
- 2 feeds have data quality issues that reduce effective coverage (me_mdm zero MACs, me_ec fake serials)
- Export CSV shows 49.9% serial_number coverage and 48.9% mac_clean coverage across all sources
- 6 ME EC records have fake serials correctly rejected by normalizer

---

## 1. Per-Feed Field Extraction Coverage

### 1.1 SharePoint Devices (sp_devices) — 581 rows

| Field | Manifest Path | Coverage | Status | Notes |
|-------|--------------|----------|--------|-------|
| source_id | `id` | 581/581 (100%) | ✓ | |
| name | `fields.Title` | 581/581 (100%) | ✓ | |
| serial_number | `fields.SerialNumber` | 520/581 (89.5%) | ✓ | |
| mac_address | `fields.ETHMAC`, `fields.WLANMAC` | 291/581 (50.1%) | ⚠ | 2 values are "na" (invalid) |
| asset_tag | `fields.AssetNumber` | 581/581 (100%) | ✓ | |
| manufacturer | `fields.ManufacturerString` | 581/581 (100%) | ✓ | |
| model | `fields.Model` | 573/581 (98.6%) | ✓ | |
| assigned_user | `fields.AssignedUserString` | 292/581 (50.3%) | ⚠ | **289 records have literal "null" string** |
| os | `fields.OperatingSystem` | 82/581 (14.1%) | ⚠ | Low coverage — SharePoint data incomplete |

**Issues:**
- `assigned_user`: 289/581 records (49.7%) have the literal string `"null"` instead of actual null. The walker's sentinel stripping correctly filters these, but this indicates a data quality issue in SharePoint.
- `mac_address`: 2 records have `"na"` as the MAC value, which fails validation (not 12 hex chars).

---

### 1.2 ManageEngine Endpoint Central (me_ec) — 67 rows

| Field | Manifest Path | Coverage | Status | Notes |
|-------|--------------|----------|--------|-------|
| source_id | `resource_id` | 67/67 (100%) | ✓ | |
| name | `fqdn_name` | 67/67 (100%) | ✓ | |
| serial_number | `servicetag` | 61/67 (91.0%) | ✓ | 6 fake serials rejected |
| mac_address | `mac_address` | 67/67 (100%) | ✓ | |
| manufacturer | `hardware_vendor`, `VENDOR`, `manufacturer` | **0/67 (0%)** | ✗ | **PATH MISMATCH** |
| model | `model`, `MODEL` | **0/67 (0%)** | ✗ | **PATH MISMATCH** |
| os | `os_name` | 67/67 (100%) | ✓ | |
| os_version | `os_version` | 67/67 (100%) | ✓ | |
| assigned_user | `agent_logged_on_users` | 31/67 (46.3%) | ⚠ | 36 records have sentinel `"--"` |
| ip_address | `ip_address` | 67/67 (100%) | ✓ | |

**Critical Issues:**
- **manufacturer/model**: Manifest expects `hardware_vendor`, `VENDOR`, `manufacturer`, `model`, `MODEL` but the ME Endpoint Central API **does not return these fields at all**. The actual API response contains 40 fields, none of which are manufacturer or model. This is a **missing data problem**, not a path mismatch.
- **serial_number**: 6 records have fake serials matching pattern `^\d{4}-\d{4}-\d{4}-\d{4}-\d{4}-\d{4}-\d{2}$` (e.g., `"6597-0669-6354-3459-2088-5723-04"`). The normalizer correctly rejects these, reducing coverage from 100% to 91%.
- **assigned_user**: 36/67 records (53.7%) have sentinel value `"--"`, correctly stripped by walker.

---

### 1.3 ManageEngine MDM (me_mdm) — 109 rows

| Field | Manifest Path | Coverage | Status | Notes |
|-------|--------------|----------|--------|-------|
| source_id | `device_id` | 109/109 (100%) | ✓ | |
| name | `device_name` | 102/109 (93.6%) | ✓ | |
| serial_number | `serial_number` | 102/109 (93.6%) | ✓ | |
| mac_address | `wifi_mac` | 102/109 (93.6%) | ✓ | 35 are zero MACs |
| manufacturer | `product_name` | 102/109 (93.6%) | ✓ | |
| model | `model` | 102/109 (93.6%) | ✓ | |
| os | `platform_type` | 102/109 (93.6%) | ✓ | |
| os_version | `os_version` | 102/109 (93.6%) | ✓ | |
| assigned_user | `user.user_email` | 102/109 (93.6%) | ✓ | |
| imei | `imei` | 70/109 (64.2%) | ✓ | List format, first_of_list transform |
| ip_address | *(not in spec)* | 0/109 (0%) | ✗ | **Not in manifest spec** |

**Issues:**
- **mac_address**: 35/102 records (34.3%) have zero MAC `00-00-00-00-00-00`, correctly rejected by normalizer. Effective coverage: 67/109 (61.5%).
- **imei**: API returns list format (e.g., `['354622790209855']`). Walker converts to comma-separated string, then `first_of_list` transform extracts first element. This works correctly.
- **ip_address**: Not in manifest spec — MDM API doesn't provide IP addresses.

---

### 1.4 FortiGate Clients (fg_clients) — 219 rows

| Field | Manifest Path | Coverage | Status | Notes |
|-------|--------------|----------|--------|-------|
| source_id | `mac` | 219/219 (100%) | ✓ | |
| name | `hostname` | 181/219 (82.6%) | ✓ | |
| mac_address | `mac` | 219/219 (100%) | ✓ | |
| ip_address | `ipv4_address` | 213/219 (97.3%) | ✓ | |
| manufacturer | `hardware_vendor` | 172/219 (78.5%) | ✓ | |
| model | `hardware_family`, `hardware_type` | 113/219 (51.6%) | ✓ | |
| os | `os_name` | 162/219 (74.0%) | ✓ | |
| os_version | `os_version` | 98/219 (44.7%) | ⚠ | Low coverage |
| assigned_user | `unauth_user` | 36/219 (16.4%) | ⚠ | Low coverage |

**Notes:**
- All paths match API response structure correctly.
- Low `assigned_user` coverage is expected — FortiGate only tracks authenticated users for some devices.

---

### 1.5 FortiGate DHCP (fg_dhcp) — 29 rows

| Field | Manifest Path | Coverage | Status | Notes |
|-------|--------------|----------|--------|-------|
| source_id | `mac` | 29/29 (100%) | ✓ | |
| name | `hostname` | 27/29 (93.1%) | ✓ | |
| mac_address | `mac` | 29/29 (100%) | ✓ | |
| ip_address | `ip` | 29/29 (100%) | ✓ | |

**Status:** All fields extract correctly.

---

### 1.6 Zabbix Hosts (zbx_hosts) — 73 rows

| Field | Manifest Path | Coverage | Status | Notes |
|-------|--------------|----------|--------|-------|
| source_id | `hostid` | 73/73 (100%) | ✓ | |
| name | `host` | 73/73 (100%) | ✓ | |
| ip_address | `interfaces.0.ip` | 71/73 (97.3%) | ✓ | |
| os | `inventory.os` | 32/73 (43.8%) | ⚠ | Low coverage |
| serial_number | `inventory.serial` | **0/73 (0%)** | ✗ | **PATH MISMATCH** |
| mac_address | `inventory.mac` | **0/73 (0%)** | ✗ | **PATH MISMATCH** |

**Critical Path Mismatches:**
- **serial_number**: Manifest expects `inventory.serial` but API returns `inventory.serial_no_a` and `inventory.serial_no_b`. Both are empty in all 73 records, so even with corrected paths, coverage would be 0%.
- **mac_address**: Manifest expects `inventory.mac` but API returns `inventory.macaddress_a` and `inventory.macaddress_b`. Both are empty in all 73 records, so even with corrected paths, coverage would be 0%.

**Root Cause:** The Zabbix API inventory object uses `_a` and `_b` suffixes for dual-slot inventory fields (e.g., two serial number slots, two MAC address slots). The manifest paths don't account for this.

---

### 1.7 Active Directory Computers (ad_computers) — 104 rows

| Field | Manifest Path | Coverage | Status | Notes |
|-------|--------------|----------|--------|-------|
| source_id | `sAMAccountName` | 104/104 (100%) | ✓ | |
| name | `dNSHostName` | 104/104 (100%) | ✓ | |
| manufacturer | `manufacturer` | **0/104 (0%)** | ✗ | **Missing data** |
| model | `model` | **0/104 (0%)** | ✗ | **Missing data** |
| os | `operatingSystem` | 104/104 (100%) | ✓ | |
| os_version | `operatingSystemVersion` | 104/104 (100%) | ✓ | |
| assigned_user | `managedBy` | 104/104 (100%) | ✓ | |
| ip_address | *(not in spec)* | 0/104 (0%) | ✗ | **Not in manifest spec** |
| mac_address | *(not in spec)* | 0/104 (0%) | ✗ | **Not in manifest spec** |

**Issues:**
- **manufacturer/model**: AD LDAP query doesn't request these attributes. The API response contains 9 attributes, none of which are manufacturer or model. This is a **missing data problem** — AD typically doesn't track hardware manufacturer/model.
- **ip_address/mac_address**: Not in manifest spec. AD doesn't typically track these either.

---

### 1.8 ServiceDesk Plus Assets (sdp_assets) — 188 rows

| Field | Manifest Path | Coverage | Status | Notes |
|-------|--------------|----------|--------|-------|
| source_id | `id` | 188/188 (100%) | ✓ | |
| name | `name` | 188/188 (100%) | ✓ | |
| manufacturer | `product.manufacturer` | 88/188 (46.8%) | ⚠ | 76 have sentinel `"-"` |
| model | `product.name` | 188/188 (100%) | ✓ | |
| assigned_user | `created_by.name` | 188/188 (100%) | ✓ | |
| serial_number | *(not in spec)* | 0/188 (0%) | ✗ | **Not in manifest spec** |
| mac_address | *(not in spec)* | 0/188 (0%) | ✗ | **Not in manifest spec** |
| os | *(not in spec)* | 0/188 (0%) | ✗ | **Not in manifest spec** |

**Issues:**
- **manufacturer**: 76/188 records (40.4%) have sentinel value `"-"` in `product.manufacturer`. The normalizer correctly strips these, reducing effective coverage from 87.2% to 46.8%.
- **serial_number/mac_address/os**: Not in manifest spec — SDP assets don't typically track these fields.

---

### 1.9 Zabbix Items (zbx_items) — 17,280 rows

| Field | Manifest Path | Coverage | Status | Notes |
|-------|--------------|----------|--------|-------|
| name | `name` | 17,280/17,280 (100%) | ✓ | |
| value | `lastvalue` | 13,924/17,280 (80.6%) | ✓ | |

**Status:** All fields extract correctly. This is an ATTACHMENT feed, not a device feed.

---

### 1.10 ServiceDesk Plus Requests (sdp_requests) — 30 rows

| Field | Manifest Path | Coverage | Status | Notes |
|-------|--------------|----------|--------|-------|
| subject | `subject` | 30/30 (100%) | ✓ | |
| status | `status.name` | 30/30 (100%) | ✓ | |

**Status:** All fields extract correctly. This is an ATTACHMENT feed, not a device feed.

---

## 2. Export CSV Coverage Analysis

**File:** `export/csv/devices.csv`  
**Total rows:** 1,370 devices across 8 sources

### 2.1 Overall Column Coverage

| Column | Non-empty | Coverage | Status |
|--------|-----------|----------|--------|
| source | 1,370/1,370 | 100.0% | ✓ |
| source_id | 1,370/1,370 | 100.0% | ✓ |
| name | 1,323/1,370 | 96.6% | ✓ |
| name_clean | 1,323/1,370 | 96.6% | ✓ |
| serial_number | 683/1,370 | 49.9% | ⚠ |
| mac_address | 708/1,370 | 51.7% | ✓ |
| mac_clean | 670/1,370 | 48.9% | ⚠ |
| asset_tag | 581/1,370 | 42.4% | ⚠ |
| manufacturer | 912/1,370 | 66.6% | ✓ |
| model | 976/1,370 | 71.2% | ✓ |
| os | 549/1,370 | 40.1% | ⚠ |
| os_version | 371/1,370 | 27.1% | ⚠ |
| assigned_user | 753/1,370 | 55.0% | ✓ |
| ip_address | 380/1,370 | 27.7% | ⚠ |
| imei | 70/1,370 | 5.1% | ⚠ |
| extra_attributes | 1,370/1,370 | 100.0% | ✓ |

### 2.2 Per-Source Coverage Breakdown

#### SharePoint (sp) — 581 rows
- **Strong:** name (100%), serial_number (89.5%), manufacturer (94.7%), model (98.6%)
- **Weak:** os (14.1%), assigned_user (50.3% — "null" string issue), mac_address (50.1%)
- **Missing:** ip_address, imei (not in spec)

#### ManageEngine EC (me_ec) — 67 rows
- **Strong:** name (100%), serial_number (91.0%), mac_address (100%), os (100%), ip_address (100%)
- **Missing:** manufacturer (0%), model (0%) — API doesn't provide these fields
- **Weak:** assigned_user (46.3% — sentinel "--" issue)

#### ManageEngine MDM (me_mdm) — 109 rows
- **Strong:** name (93.6%), serial_number (93.6%), manufacturer (93.6%), model (93.6%), os (93.6%), assigned_user (93.6%), imei (64.2%)
- **Weak:** mac_clean (61.5% — 35 zero MACs rejected)
- **Missing:** ip_address (not in spec)

#### FortiGate (fg) — 219 rows
- **Strong:** mac_address (100%), ip_address (97.3%), manufacturer (78.5%)
- **Weak:** name (82.6%), model (51.6%), os (74.0%), assigned_user (16.4%)
- **Missing:** serial_number (not in spec), imei (not in spec)

#### FortiGate DHCP (fg_dhcp) — 29 rows
- **Strong:** name (93.1%), mac_address (100%), ip_address (100%)
- **Missing:** serial_number, manufacturer, model, os, assigned_user, imei (not in spec)

#### Zabbix (zbx) — 73 rows
- **Strong:** name (100%), ip_address (97.3%)
- **Weak:** os (43.8%)
- **Missing:** serial_number (0% — path mismatch), mac_address (0% — path mismatch), manufacturer, model, assigned_user, imei (not in spec)

#### Active Directory (ad) — 104 rows
- **Strong:** name (100%), os (100%), assigned_user (100%)
- **Missing:** serial_number (0%), mac_address (0%), manufacturer (0%), model (0%), ip_address (0%), imei (0%) — AD doesn't track these

#### ServiceDesk Plus (sdp) — 188 rows
- **Strong:** name (100%), model (100%), assigned_user (100%)
- **Weak:** manufacturer (46.8% — sentinel "-" issue)
- **Missing:** serial_number, mac_address, os, ip_address, imei (not in spec)

---

## 3. Critical Field Path Mismatches

### 3.1 Zabbix Hosts: serial_number and mac_address

**Manifest paths:**
- `serial_number`: `inventory.serial`
- `mac_address`: `inventory.mac`

**Actual API response:**
- `inventory.serial_no_a`, `inventory.serial_no_b`
- `inventory.macaddress_a`, `inventory.macaddress_b`

**Impact:** 0/73 records extract serial_number or mac_address.

**Root Cause:** Zabbix inventory uses `_a` and `_b` suffixes for dual-slot inventory fields. The manifest doesn't account for this.

**Data Reality:** Even if paths were corrected, all 73 records have empty values for `serial_no_a`, `serial_no_b`, `macaddress_a`, `macaddress_b`. The Zabbix inventory population is incomplete.

**Recommendation:** 
1. Update manifest paths to `inventory.serial_no_a`, `inventory.serial_no_b`, `inventory.macaddress_a`, `inventory.macaddress_b`.
2. Investigate why Zabbix inventory is empty — may need to enable inventory population in Zabbix agent configuration.

---

### 3.2 ManageEngine EC: manufacturer and model

**Manifest paths:**
- `manufacturer`: `hardware_vendor`, `VENDOR`, `manufacturer`
- `model`: `model`, `MODEL`

**Actual API response:**
The ME Endpoint Central `/inventory/scancomputers` API returns 40 fields, **none of which are manufacturer or model**. The API focuses on software inventory and OS details, not hardware attributes.

**Impact:** 0/67 records extract manufacturer or model.

**Root Cause:** The ME Endpoint Central API doesn't provide hardware manufacturer/model data. This is a **missing data problem**, not a path mismatch.

**Recommendation:**
1. Remove `manufacturer` and `model` from the `me_ec` manifest spec — these fields are not available from this API.
2. If hardware manufacturer/model is required, consider using a different ME API endpoint or a different source (e.g., ME MDM provides `product_name` and `model`).

---

### 3.3 SharePoint Devices: assigned_user "null" string

**Manifest path:** `fields.AssignedUserString`

**Actual API response:**
- 292/581 records have real user names (e.g., "Jeremy Johann")
- 289/581 records have the literal string `"null"` (not actual null)

**Impact:** Effective coverage is 50.3%, not 100%. The walker's sentinel stripping correctly filters the `"null"` strings, but this indicates a data quality issue in SharePoint.

**Root Cause:** SharePoint list has a default value of `"null"` for the `AssignedUserString` field when no user is assigned.

**Recommendation:**
1. No code change needed — the walker correctly handles this via sentinel stripping.
2. Consider fixing the SharePoint list to use actual null instead of the string `"null"`.

---

## 4. Data Quality Issues

### 4.1 ManageEngine MDM: Zero MAC Addresses

**Field:** `wifi_mac`  
**Issue:** 35/102 records (34.3%) have zero MAC `00-00-00-00-00-00`.

**Impact:** The normalizer correctly rejects zero MACs (via `_NULL_MAC_RE` regex), reducing effective mac_clean coverage from 93.6% to 61.5%.

**Root Cause:** ME MDM reports zero MAC for devices that haven't connected yet, or for devices with disabled WiFi interfaces.

**Status:** Handled correctly by normalizer. No code change needed.

---

### 4.2 ManageEngine EC: Fake Serial Numbers

**Field:** `servicetag`  
**Issue:** 6/67 records (9.0%) have fake serials matching pattern `^\d{4}-\d{4}-\d{4}-\d{4}-\d{4}-\d{4}-\d{2}$`.

**Examples:**
- `"6597-0669-6354-3459-2088-5723-04"`
- `"1678-8689-4003-2107-3109-8038-99"`

**Impact:** The normalizer correctly rejects fake serials (via `_ME_FAKE_SERIAL_RE` regex), reducing effective serial_number coverage from 100% to 91.0%.

**Root Cause:** ME Endpoint Central generates fake serials when the real serial is unavailable.

**Status:** Handled correctly by normalizer. No code change needed.

---

### 4.3 ServiceDesk Plus Assets: Sentinel Manufacturer

**Field:** `product.manufacturer`  
**Issue:** 76/188 records (40.4%) have sentinel value `"-"`.

**Impact:** The normalizer strips sentinels, reducing effective manufacturer coverage from 87.2% to 46.8%.

**Root Cause:** SDP uses `"-"` as a placeholder when manufacturer is unknown.

**Status:** Handled correctly by normalizer. No code change needed.

---

### 4.4 ManageEngine EC: Sentinel Assigned User

**Field:** `agent_logged_on_users`  
**Issue:** 36/67 records (53.7%) have sentinel value `"--"`.

**Impact:** The normalizer strips sentinels, reducing effective assigned_user coverage from 100% to 46.3%.

**Root Cause:** ME EC uses `"--"` when no user is logged in.

**Status:** Handled correctly by normalizer. No code change needed.

---

## 5. Walker Behavior Verification

### 5.1 List-to-String Conversion

**Test Case:** ME MDM `imei` field  
**Raw value:** `['354622790209855']` (list)  
**Walker behavior:** Converts list to comma-separated string `"354622790209855"`, then applies `first_of_list` transform.  
**Result:** ✓ Correct — extracts first IMEI from list.

**Test Case:** ME MDM `imei` with multiple values  
**Raw value:** `['354666655016848', '354666654974153']`  
**Walker behavior:** Converts to `"354666655016848,354666654974153"`, then `first_of_list` extracts `"354666655016848"`.  
**Result:** ✓ Correct — extracts first IMEI.

---

### 5.2 Sentinel Stripping

**Test Case:** SharePoint `assigned_user` with `"null"` string  
**Raw value:** `"null"`  
**Walker behavior:** `strip_sentinels()` recognizes `"null"` as a sentinel and returns `""`.  
**Result:** ✓ Correct — filters out placeholder values.

**Test Case:** ME EC `assigned_user` with `"--"`  
**Raw value:** `"--"`  
**Walker behavior:** `strip_sentinels()` recognizes `"--"` as a sentinel and returns `""`.  
**Result:** ✓ Correct.

---

### 5.3 MAC Address Normalization

**Test Case:** ME MDM zero MAC  
**Raw value:** `"00-00-00-00-00-00"`  
**Normalizer behavior:** `_NULL_MAC_RE` regex matches all-zero MACs and returns `""`.  
**Result:** ✓ Correct — prevents false matches in Splink.

**Test Case:** SharePoint invalid MAC  
**Raw value:** `"na"`  
**Normalizer behavior:** After stripping separators, length is 2 (not 12), so `_validate_single_mac` returns `""`.  
**Result:** ✓ Correct — rejects invalid MACs.

---

### 5.4 Serial Number Normalization

**Test Case:** ME EC fake serial  
**Raw value:** `"6597-0669-6354-3459-2088-5723-04"`  
**Normalizer behavior:** `_ME_FAKE_SERIAL_RE` regex matches the pattern and returns `""`.  
**Result:** ✓ Correct — prevents fake serials from blocking.

---

## 6. Recommended Fixes

### 6.1 High Priority

#### Fix 1: Update Zabbix Hosts Manifest Paths

**File:** `projects/default/manifest.py`  
**Lines:** 266-267

**Current:**
```python
"serial_number": ("inventory.serial",),
"mac_address": ("inventory.mac",),
```

**Proposed:**
```python
"serial_number": ("inventory.serial_no_a", "inventory.serial_no_b"),
"mac_address": ("inventory.macaddress_a", "inventory.macaddress_b"),
```

**Impact:** Enables serial_number and mac_address extraction if Zabbix inventory is populated. Currently 0% coverage due to path mismatch.

**Caveat:** Even with corrected paths, coverage will remain 0% until Zabbix inventory is populated. Investigate Zabbix agent configuration.

---

#### Fix 2: Remove Unavailable Fields from ME EC Manifest

**File:** `projects/default/manifest.py`  
**Lines:** 170-171

**Current:**
```python
"manufacturer": ("hardware_vendor", "VENDOR", "manufacturer"),
"model": ("model", "MODEL"),
```

**Proposed:**
Remove these two fields from the `me_ec` spec. The ME Endpoint Central API doesn't provide hardware manufacturer/model data.

**Impact:** Eliminates misleading 0% coverage metrics. If hardware manufacturer/model is required, use ME MDM (which provides `product_name` and `model`) or another source.

---

### 6.2 Medium Priority

#### Fix 3: Investigate Zabbix Inventory Population

**Issue:** All 73 Zabbix hosts have empty `serial_no_a`, `serial_no_b`, `macaddress_a`, `macaddress_b` fields.

**Action:**
1. Check Zabbix agent configuration on monitored hosts — inventory population may be disabled.
2. Verify Zabbix server inventory settings — auto-inventory population may be turned off.
3. Consider using alternative sources for serial/MAC data (ME EC, ME MDM, SharePoint).

---

#### Fix 4: Add IP Address to ME MDM Manifest (If Available)

**Issue:** ME MDM doesn't provide IP addresses, but this limits cross-source matching.

**Action:**
1. Verify ME MDM API documentation — check if IP address is available in a different endpoint.
2. If not available, document this limitation in the manifest comments.

---

### 6.3 Low Priority

#### Fix 5: Improve SharePoint Data Quality

**Issue:** 289/581 SharePoint records have `"null"` string instead of actual null for `AssignedUserString`.

**Action:**
1. Update SharePoint list to use actual null instead of `"null"` string.
2. No code change needed — walker handles this correctly.

---

#### Fix 6: Document AD Limitations

**Issue:** AD doesn't track serial_number, mac_address, manufacturer, model, ip_address.

**Action:**
1. Add comments to `ad_computers` manifest spec explaining that these fields are not available from AD LDAP.
2. Consider using alternative sources for hardware attributes.

---

## 7. Summary Statistics

### 7.1 Field Extraction Success Rate

| Feed | Fields | Extracting | Coverage |
|------|--------|------------|----------|
| sp_devices | 9 | 9 | 100% |
| me_ec | 10 | 8 | 80% |
| me_mdm | 10 | 10 | 100% |
| fg_clients | 9 | 9 | 100% |
| fg_dhcp | 4 | 4 | 100% |
| zbx_hosts | 6 | 4 | 67% |
| ad_computers | 7 | 5 | 71% |
| sdp_assets | 5 | 5 | 100% |
| zbx_items | 2 | 2 | 100% |
| sdp_requests | 2 | 2 | 100% |
| **Total** | **64** | **58** | **90.6%** |

### 7.2 Export CSV Coverage

| Metric | Value |
|--------|-------|
| Total devices | 1,370 |
| Devices with serial_number | 683 (49.9%) |
| Devices with mac_clean | 670 (48.9%) |
| Devices with name | 1,323 (96.6%) |
| Devices with manufacturer | 912 (66.6%) |
| Devices with model | 976 (71.2%) |
| Devices with os | 549 (40.1%) |
| Devices with assigned_user | 753 (55.0%) |
| Devices with ip_address | 380 (27.7%) |

### 7.3 Critical Issues Requiring Action

1. **Zabbix hosts serial/mac path mismatch** — 0/73 records extract (path mismatch + empty inventory)
2. **ME EC manufacturer/model missing** — 0/67 records extract (API doesn't provide)
3. **SharePoint assigned_user "null" string** — 289/581 records have placeholder (handled by walker)

---

## 8. Conclusion

The field mapping audit reveals that **90.6% of manifest fields extract successfully**, but **3 critical path mismatches** and **2 data quality issues** significantly reduce coverage for specific feeds. The walker and normalizer correctly handle sentinel values, list-to-string conversion, and invalid MAC/serial rejection.

**Key Actions:**
1. Fix Zabbix hosts manifest paths (serial_no_a/b, macaddress_a/b)
2. Remove unavailable fields from ME EC manifest (manufacturer, model)
3. Investigate Zabbix inventory population
4. Document AD and SDP limitations

**Overall Assessment:** The manifest-driven pipeline is functioning correctly, but manifest paths need alignment with actual API response structures for 3 feeds.
