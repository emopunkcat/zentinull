"""Unit tests for manifest module."""

import pytest

from zentinull.manifest import (
    ManifestValidationError,
    get_anchor_feeds,
    get_feed_keys,
    get_system_feeds,
    load_manifest,
)
from zentinull.manifest.types import (
    Auth,
    Comparison,
    Feed,
    Link,
    Manifest,
    ResolutionProfile,
    Role,
    System,
)


class TestManifestTypes:
    """Test manifest dataclass types."""

    def test_role_enum(self):
        """Role enum has three values."""
        assert Role.ANCHOR.value == "anchor"
        assert Role.ATTACHMENT.value == "attachment"
        assert Role.CONTEXT.value == "context"

    def test_auth_frozen(self):
        """Auth is frozen (immutable)."""
        auth = Auth(kind="api_key", options={"token": "API_TOKEN"})
        with pytest.raises(AttributeError):
            auth.kind = "oauth"  # type: ignore

    def test_system_creation(self):
        """System can be created with all fields."""
        system = System(
            auth=Auth(kind="api_key", options={"token": "API_TOKEN"}),
            strategy="rest_json",
            label="Test System",
            schedule=3600,
            options={"base_url": "https://api.example.com"},
        )
        assert system.auth.kind == "api_key"
        assert system.strategy == "rest_json"
        assert system.label == "Test System"
        assert system.schedule == 3600
        assert system.options["base_url"] == "https://api.example.com"

    def test_feed_creation(self):
        """Feed can be created with all fields."""
        feed = Feed(
            system="test",
            endpoint={"path": "/api/devices"},
            role=Role.ANCHOR,
            profile="device",
            store="test_devices",
            id_path="id",
            updated_path="updated_at",
            spec={
                "source_id": ("id",),
                "name": ("hostname",),
            },
        )
        assert feed.system == "test"
        assert feed.role == Role.ANCHOR
        assert feed.profile == "device"
        assert feed.store == "test_devices"
        assert feed.id_path == "id"
        assert "source_id" in feed.spec
        assert feed.spec["source_id"] == ("id",)

    def test_link_creation(self):
        """Link can be created with all fields."""
        link = Link(
            field="hostid",
            to="device",
            on="source_id",
            scope=("zbx_hosts",),
        )
        assert link.field == "hostid"
        assert link.to == "device"
        assert link.on == "source_id"
        assert link.scope == ("zbx_hosts",)

    def test_comparison_creation(self):
        """Comparison can be created with all fields."""
        comp = Comparison(
            kind="levenshtein",
            column="serial_number",
            thresholds=(1, 2),
            term_frequency_adjustments=True,
        )
        assert comp.kind == "levenshtein"
        assert comp.column == "serial_number"
        assert comp.thresholds == (1, 2)
        assert comp.term_frequency_adjustments is True

    def test_resolution_profile_creation(self):
        """ResolutionProfile can be created with all fields."""
        profile = ResolutionProfile(
            name="device",
            fields=("source", "source_id", "name"),
            derived={"name_clean": ("name", "name")},
            comparisons=(Comparison(kind="exact", column="name_clean"),),
            blocking=("name_clean",),
            deterministic=("name_clean",),
            em_passes=("name_clean",),
            predict_threshold=-10.0,
            cluster_threshold=-5.0,
            sweep_thresholds=(10, 5, 0, -5, -10),
            u_max_pairs=2000000,
            lambda_recall=0.5,
        )
        assert profile.name == "device"
        assert len(profile.fields) == 3
        assert "name_clean" in profile.derived
        assert len(profile.comparisons) == 1
        assert profile.predict_threshold == -10.0
        assert profile.lambda_recall == 0.5

    def test_manifest_creation(self):
        """Manifest can be created with all fields."""
        manifest = Manifest(
            project="test",
            systems={
                "test": System(
                    auth=Auth(kind="none"),
                    strategy="rest_json",
                ),
            },
            feeds={
                "test_devices": Feed(
                    system="test",
                    endpoint={"path": "/api/devices"},
                    role=Role.ANCHOR,
                    profile="device",
                    store="test_devices",
                    id_path="id",
                ),
            },
            profiles={
                "device": ResolutionProfile(
                    name="device",
                    fields=("source", "source_id"),
                    derived={},
                    comparisons=(),
                    blocking=(),
                    deterministic=(),
                    em_passes=(),
                    predict_threshold=-10.0,
                    cluster_threshold=-5.0,
                ),
            },
        )
        assert manifest.project == "test"
        assert "test" in manifest.systems
        assert "test_devices" in manifest.feeds
        assert "device" in manifest.profiles


class TestManifestValidation:
    """Test manifest validation logic."""

    def _make_valid_manifest(self) -> Manifest:
        """Create a minimal valid manifest for testing."""
        from zentinull.manifest.types import FieldSpec

        return Manifest(
            project="test",
            systems={
                "test": System(
                    auth=Auth(kind="none"),
                    strategy="rest_json",
                ),
            },
            feeds={
                "test_devices": Feed(
                    system="test",
                    endpoint={"path": "/api/devices"},
                    role=Role.ANCHOR,
                    profile="device",
                    store="test_devices",
                    id_path="id",
                    spec={
                        "source_id": FieldSpec(("id",)),
                        "name": FieldSpec(("hostname",)),
                    },
                ),
            },
            profiles={
                "device": ResolutionProfile(
                    name="device",
                    fields=("source", "source_id", "name"),
                    derived={"name_clean": ("name", "name")},
                    comparisons=(Comparison(kind="exact", column="name_clean"),),
                    blocking=("name_clean",),
                    deterministic=("name_clean",),
                    em_passes=("name_clean",),
                    predict_threshold=-10.0,
                    cluster_threshold=-5.0,
                ),
            },
        )

    def test_valid_manifest_passes(self):
        """Valid manifest passes validation."""
        from zentinull.manifest import _validate

        manifest = self._make_valid_manifest()
        _validate(manifest)  # Should not raise

    def test_feed_references_unknown_system(self):
        """Feed referencing unknown system fails validation."""
        from zentinull.manifest import _validate
        from zentinull.manifest.types import FieldSpec

        manifest = self._make_valid_manifest()
        manifest = Manifest(
            project=manifest.project,
            systems=manifest.systems,
            feeds={
                **manifest.feeds,
                "bad_feed": Feed(
                    system="nonexistent",
                    endpoint={"path": "/api/bad"},
                    role=Role.CONTEXT,
                    store="bad",
                    id_path="id",
                    spec={"source_id": FieldSpec(("id",))},
                ),
            },
            profiles=manifest.profiles,
        )
        with pytest.raises(ManifestValidationError, match="references unknown system"):
            _validate(manifest)

    def test_anchor_feed_missing_profile(self):
        """ANCHOR feed without profile fails validation."""
        from zentinull.manifest import _validate
        from zentinull.manifest.types import FieldSpec

        manifest = self._make_valid_manifest()
        manifest = Manifest(
            project=manifest.project,
            systems=manifest.systems,
            feeds={
                **manifest.feeds,
                "no_profile": Feed(
                    system="test",
                    endpoint={"path": "/api/nope"},
                    role=Role.ANCHOR,
                    profile=None,
                    store="no_profile",
                    id_path="id",
                    spec={"source_id": FieldSpec(("id",))},
                ),
            },
            profiles=manifest.profiles,
        )
        with pytest.raises(ManifestValidationError, match="must have a profile"):
            _validate(manifest)

    def test_anchor_feed_references_unknown_profile(self):
        """ANCHOR feed referencing unknown profile fails validation."""
        from zentinull.manifest import _validate
        from zentinull.manifest.types import FieldSpec

        manifest = self._make_valid_manifest()
        manifest = Manifest(
            project=manifest.project,
            systems=manifest.systems,
            feeds={
                **manifest.feeds,
                "bad_profile": Feed(
                    system="test",
                    endpoint={"path": "/api/bad"},
                    role=Role.ANCHOR,
                    profile="nonexistent",
                    store="bad_profile",
                    id_path="id",
                    spec={"source_id": FieldSpec(("id",))},
                ),
            },
            profiles=manifest.profiles,
        )
        with pytest.raises(ManifestValidationError, match="references unknown profile"):
            _validate(manifest)

    def test_link_references_unknown_profile(self):
        """Link referencing unknown profile fails validation."""
        from zentinull.manifest import _validate
        from zentinull.manifest.types import FieldSpec

        manifest = self._make_valid_manifest()
        manifest = Manifest(
            project=manifest.project,
            systems=manifest.systems,
            feeds={
                **manifest.feeds,
                "with_link": Feed(
                    system="test",
                    endpoint={"path": "/api/items"},
                    role=Role.ATTACHMENT,
                    store="items",
                    id_path="itemid",
                    spec={"source_id": FieldSpec(("itemid",))},
                    links=(Link(field="hostid", to="nonexistent", on="source_id"),),
                ),
            },
            profiles=manifest.profiles,
        )
        with pytest.raises(ManifestValidationError, match="link references unknown profile"):
            _validate(manifest)

    def test_spec_key_not_in_profile(self):
        """Spec key not in profile fields fails validation."""
        from zentinull.manifest import _validate
        from zentinull.manifest.types import FieldSpec

        manifest = self._make_valid_manifest()
        old_feed = manifest.feeds["test_devices"]
        manifest = Manifest(
            project=manifest.project,
            systems=manifest.systems,
            feeds={
                **manifest.feeds,
                "test_devices": Feed(
                    system=old_feed.system,
                    endpoint=old_feed.endpoint,
                    role=old_feed.role,
                    profile=old_feed.profile,
                    store=old_feed.store,
                    id_path=old_feed.id_path,
                    updated_path=old_feed.updated_path,
                    spec={**old_feed.spec, "bad_field": FieldSpec(("bad",))},
                    links=old_feed.links,
                ),
            },
            profiles=manifest.profiles,
        )
        with pytest.raises(ManifestValidationError, match="spec key.*not in profile"):
            _validate(manifest)

    def test_link_on_not_in_target_profile(self):
        """Link.on not in target profile fields fails validation."""
        from zentinull.manifest import _validate
        from zentinull.manifest.types import FieldSpec

        manifest = self._make_valid_manifest()
        manifest = Manifest(
            project=manifest.project,
            systems=manifest.systems,
            feeds={
                **manifest.feeds,
                "with_link": Feed(
                    system="test",
                    endpoint={"path": "/api/items"},
                    role=Role.ATTACHMENT,
                    store="items",
                    id_path="itemid",
                    spec={"source_id": FieldSpec(("itemid",))},
                    links=(Link(field="hostid", to="device", on="nonexistent"),),
                ),
            },
            profiles=manifest.profiles,
        )
        with pytest.raises(ManifestValidationError, match="link.on.*not in target profile"):
            _validate(manifest)

    def test_link_scope_not_anchor_feed(self):
        """Link.scope referencing non-ANCHOR feed fails validation."""
        from zentinull.manifest import _validate
        from zentinull.manifest.types import FieldSpec

        manifest = self._make_valid_manifest()
        manifest = Manifest(
            project=manifest.project,
            systems=manifest.systems,
            feeds={
                **manifest.feeds,
                "context_feed": Feed(
                    system="test",
                    endpoint={"path": "/api/context"},
                    role=Role.CONTEXT,
                    store="context",
                    id_path="id",
                    spec={"source_id": FieldSpec(("id",))},
                ),
                "with_link": Feed(
                    system="test",
                    endpoint={"path": "/api/items"},
                    role=Role.ATTACHMENT,
                    store="items",
                    id_path="itemid",
                    spec={"source_id": FieldSpec(("itemid",))},
                    links=(Link(field="hostid", to="device", on="source_id", scope=("context_feed",)),),
                ),
            },
            profiles=manifest.profiles,
        )
        with pytest.raises(ManifestValidationError, match="link.scope.*is not an ANCHOR feed"):
            _validate(manifest)

    def test_unknown_transform(self):
        """Spec referencing unknown transform fails validation."""
        from zentinull.manifest import _validate
        from zentinull.manifest.types import FieldSpec

        manifest = self._make_valid_manifest()
        old_feed = manifest.feeds["test_devices"]
        manifest = Manifest(
            project=manifest.project,
            systems=manifest.systems,
            feeds={
                **manifest.feeds,
                "test_devices": Feed(
                    system=old_feed.system,
                    endpoint=old_feed.endpoint,
                    role=old_feed.role,
                    profile=old_feed.profile,
                    store=old_feed.store,
                    id_path=old_feed.id_path,
                    updated_path=old_feed.updated_path,
                    spec={**old_feed.spec, "name": FieldSpec(("hostname",), transform="nonexistent")},
                    links=old_feed.links,
                ),
            },
            profiles=manifest.profiles,
        )
        with pytest.raises(ManifestValidationError, match="references unknown transform"):
            _validate(manifest)

    def test_anchor_feed_missing_id_path(self):
        """ANCHOR feed without id_path fails validation."""
        from zentinull.manifest import _validate
        from zentinull.manifest.types import FieldSpec

        manifest = self._make_valid_manifest()
        manifest = Manifest(
            project=manifest.project,
            systems=manifest.systems,
            feeds={
                **manifest.feeds,
                "no_id": Feed(
                    system="test",
                    endpoint={"path": "/api/nope"},
                    role=Role.ANCHOR,
                    profile="device",
                    store="no_id",
                    id_path="",
                    spec={"source_id": FieldSpec(("id",))},
                ),
            },
            profiles=manifest.profiles,
        )
        with pytest.raises(ManifestValidationError, match="must have non-empty id_path"):
            _validate(manifest)

    def test_blocking_column_not_in_profile(self):
        """Blocking column not in profile fields fails validation."""
        from zentinull.manifest import _validate

        manifest = self._make_valid_manifest()
        old_profile = manifest.profiles["device"]
        manifest = Manifest(
            project=manifest.project,
            systems=manifest.systems,
            feeds=manifest.feeds,
            profiles={
                "device": ResolutionProfile(
                    name=old_profile.name,
                    fields=old_profile.fields,
                    derived=old_profile.derived,
                    comparisons=old_profile.comparisons,
                    blocking=("nonexistent",),
                    deterministic=old_profile.deterministic,
                    em_passes=old_profile.em_passes,
                    predict_threshold=old_profile.predict_threshold,
                    cluster_threshold=old_profile.cluster_threshold,
                    sweep_thresholds=old_profile.sweep_thresholds,
                    u_max_pairs=old_profile.u_max_pairs,
                    lambda_recall=old_profile.lambda_recall,
                ),
            },
        )
        with pytest.raises(ManifestValidationError, match="blocking column.*not in fields"):
            _validate(manifest)


class TestManifestHelpers:
    """Test manifest helper functions."""

    def _make_test_manifest(self) -> Manifest:
        """Create a test manifest with multiple feeds."""
        return Manifest(
            project="test",
            systems={
                "sys1": System(auth=Auth(kind="none"), strategy="rest_json"),
                "sys2": System(auth=Auth(kind="none"), strategy="rest_json"),
            },
            feeds={
                "sys1_devices": Feed(
                    system="sys1",
                    endpoint={"path": "/api/devices"},
                    role=Role.ANCHOR,
                    profile="device",
                    store="sys1_devices",
                    id_path="id",
                ),
                "sys1_items": Feed(
                    system="sys1",
                    endpoint={"path": "/api/items"},
                    role=Role.ATTACHMENT,
                    store="sys1_items",
                    id_path="itemid",
                ),
                "sys2_devices": Feed(
                    system="sys2",
                    endpoint={"path": "/api/devices"},
                    role=Role.ANCHOR,
                    profile="device",
                    store="sys2_devices",
                    id_path="id",
                ),
                "sys2_logs": Feed(
                    system="sys2",
                    endpoint={"path": "/api/logs"},
                    role=Role.CONTEXT,
                    store="sys2_logs",
                    id_path="id",
                ),
            },
            profiles={
                "device": ResolutionProfile(
                    name="device",
                    fields=("source", "source_id"),
                    derived={},
                    comparisons=(),
                    blocking=(),
                    deterministic=(),
                    em_passes=(),
                    predict_threshold=-10.0,
                    cluster_threshold=-5.0,
                ),
            },
        )

    def test_get_feed_keys_all(self):
        """get_feed_keys returns all feeds when no role specified."""
        manifest = self._make_test_manifest()
        keys = get_feed_keys(manifest)
        assert set(keys) == {"sys1_devices", "sys1_items", "sys2_devices", "sys2_logs"}

    def test_get_feed_keys_by_role(self):
        """get_feed_keys filters by role."""
        manifest = self._make_test_manifest()
        anchor_keys = get_feed_keys(manifest, Role.ANCHOR)
        assert set(anchor_keys) == {"sys1_devices", "sys2_devices"}

        attachment_keys = get_feed_keys(manifest, Role.ATTACHMENT)
        assert attachment_keys == ["sys1_items"]

        context_keys = get_feed_keys(manifest, Role.CONTEXT)
        assert context_keys == ["sys2_logs"]

    def test_get_anchor_feeds_all(self):
        """get_anchor_feeds returns all ANCHOR feeds."""
        manifest = self._make_test_manifest()
        keys = get_anchor_feeds(manifest)
        assert set(keys) == {"sys1_devices", "sys2_devices"}

    def test_get_anchor_feeds_by_profile(self):
        """get_anchor_feeds filters by profile."""
        manifest = self._make_test_manifest()
        keys = get_anchor_feeds(manifest, "device")
        assert set(keys) == {"sys1_devices", "sys2_devices"}

        keys = get_anchor_feeds(manifest, "nonexistent")
        assert keys == []

    def test_get_system_feeds(self):
        """get_system_feeds returns feeds for a system."""
        manifest = self._make_test_manifest()
        sys1_keys = get_system_feeds(manifest, "sys1")
        assert set(sys1_keys) == {"sys1_devices", "sys1_items"}

        sys2_keys = get_system_feeds(manifest, "sys2")
        assert set(sys2_keys) == {"sys2_devices", "sys2_logs"}

        keys = get_system_feeds(manifest, "nonexistent")
        assert keys == []


class TestDefaultManifest:
    """Test the default project manifest."""

    def test_load_default_manifest(self):
        """Default manifest loads and validates successfully."""
        manifest = load_manifest("default")
        assert manifest.project == "default"
        assert len(manifest.systems) == 6
        assert len(manifest.feeds) == 24
        assert len(manifest.profiles) == 1
        assert "device" in manifest.profiles

    def test_default_manifest_systems(self):
        """Default manifest has all 6 expected systems."""
        manifest = load_manifest("default")
        expected_systems = {"sp", "me", "fg", "zbx", "ad", "sdp"}
        assert set(manifest.systems.keys()) == expected_systems

    def test_default_manifest_feed_roles(self):
        """Default manifest has correct feed role distribution."""
        manifest = load_manifest("default")
        anchor_feeds = get_feed_keys(manifest, Role.ANCHOR)
        attachment_feeds = get_feed_keys(manifest, Role.ATTACHMENT)
        context_feeds = get_feed_keys(manifest, Role.CONTEXT)

        assert len(anchor_feeds) == 8
        assert len(attachment_feeds) == 7
        assert len(context_feeds) == 9
        assert len(anchor_feeds) + len(attachment_feeds) + len(context_feeds) == 24

    def test_default_manifest_device_profile(self):
        """Default manifest device profile has expected structure."""
        manifest = load_manifest("default")
        profile = manifest.profiles["device"]

        assert profile.name == "device"
        assert len(profile.fields) == 23
        assert "source" in profile.fields
        assert "source_id" in profile.fields
        assert "name" in profile.fields
        assert "name_clean" in profile.derived
        assert "mac_clean" in profile.derived
        assert len(profile.comparisons) == 7
        assert "name_clean" in profile.blocking
        assert "ip_address" in [c.column for c in profile.comparisons]
        assert profile.predict_threshold < 0
        assert profile.cluster_threshold < 0

    def test_default_manifest_zbx_items_link(self):
        """Default manifest zbx_items has correct link configuration."""
        manifest = load_manifest("default")
        zbx_items = manifest.feeds["zbx_items"]

        assert zbx_items.role == Role.ATTACHMENT
        assert len(zbx_items.links) == 1
        link = zbx_items.links[0]
        assert link.field == "hostid"
        assert link.to == "device"
        assert link.on == "source_id"
        assert link.scope == ("zbx_hosts",)

    def test_default_manifest_sdp_requests_link(self):
        """Default manifest sdp_requests has extract_fuzzy link."""
        manifest = load_manifest("default")
        sdp_requests = manifest.feeds["sdp_requests"]

        assert sdp_requests.role == Role.ATTACHMENT
        assert len(sdp_requests.links) == 1
        link = sdp_requests.links[0]
        assert link.field == "subject"
        assert link.to == "device"
        assert link.on == "name"
        assert link.strategy == "extract_fuzzy"
        assert link.multi is True

    def test_default_manifest_sp_devicenotes_link(self):
        """sp_devicenotes links via SharePoint lookup ID."""
        manifest = load_manifest("default")
        feed = manifest.feeds["sp_devicenotes"]

        assert feed.role == Role.ATTACHMENT
        assert len(feed.links) == 1
        link = feed.links[0]
        assert link.field == "fields.LookupToDevicesLookupId"
        assert link.on == "source_id"
        assert link.strategy == "exact"
        assert link.scope == ("sp_devices",)

    def test_default_manifest_sp_componentpurchases_link(self):
        """sp_componentpurchases links via SharePoint lookup ID."""
        manifest = load_manifest("default")
        feed = manifest.feeds["sp_componentpurchases"]

        assert feed.role == Role.ATTACHMENT
        assert len(feed.links) == 1
        link = feed.links[0]
        assert link.field == "fields.LookupToDevicesLookupId"
        assert link.on == "source_id"
        assert link.strategy == "exact"
        assert link.scope == ("sp_devices",)

    def test_default_manifest_sp_accountinfo_link(self):
        """sp_accountinfo links via device name and employee name."""
        manifest = load_manifest("default")
        feed = manifest.feeds["sp_accountinfo"]

        assert feed.role == Role.ATTACHMENT
        assert len(feed.links) == 2
        # Link 1: DeviceString → name_clean (device name match)
        link = feed.links[0]
        assert link.field == "fields.DeviceString"
        assert link.on == "name_clean"
        assert link.strategy == "exact"
        assert link.scope == ("sp_devices",)
        # Link 2: EmployeeString → assigned_user (employee name match)
        link2 = feed.links[1]
        assert link2.field == "fields.EmployeeString"
        assert link2.on == "assigned_user"
        assert link2.strategy == "exact"

    def test_default_manifest_sp_employees_link(self):
        """sp_employees links via assigned user email and name."""
        manifest = load_manifest("default")
        feed = manifest.feeds["sp_employees"]

        assert feed.role == Role.ATTACHMENT
        assert len(feed.links) == 3
        # Link 1: email-based (ME_MDM stores email as assigned_user)
        link = feed.links[0]
        assert link.field == "fields.BusEmailAddress"
        assert link.on == "assigned_user"
        assert link.strategy == "exact"
        assert "sp_devices" in link.scope
        # Link 2: name-based (SP/FG/AD stores full name as assigned_user)
        link2 = feed.links[1]
        assert link2.field == "fields.ReadName"
        assert link2.on == "assigned_user"
        assert link2.strategy == "exact"
        # Link 3: username-based (FG stores MLUsername as assigned_user)
        link3 = feed.links[2]
        assert link3.field == "fields.MLUsername"
        assert link3.on == "assigned_user"
        assert link3.strategy == "exact"

    def test_default_manifest_sp_employeedocs_link(self):
        """sp_employeedocs links via normalized URL→employee name → assigned_user."""
        manifest = load_manifest("default")
        feed = manifest.feeds["sp_employeedocs"]

        assert feed.system == "sp"
        assert feed.role == Role.ATTACHMENT
        assert feed.id_path == "fields.ID"
        assert len(feed.links) == 1
        link = feed.links[0]
        assert link.field == "webUrl"
        assert link.on == "assigned_user"
        assert link.strategy == "normalized"
        assert link.transform == "employee_name_from_url"
        assert link.multi is True
        # Must include all anchor feeds that store assigned_user so a doc
        # links to the same clusters an employee would.
        assert "sp_devices" in link.scope
        assert "me_ec" in link.scope
        assert "me_mdm" in link.scope
        assert "fg_clients" in link.scope
        assert "ad_computers" in link.scope


class TestEmployeeNameFromUrlTransform:
    """Verify the employee_name_from_url transform used by sp_employeedocs."""

    def test_parses_basic_employee_name(self):
        from zentinull.manifest.transforms import _employee_name_from_url

        assert (
            _employee_name_from_url(
                "https://moonliteconstruction.sharepoint.com/sites/MLIT-DEV/Agreements/Rick%20Ahmed__10/221318F.001b_rahmed_MD-Issuance.pdf"
            )
            == "Rick Ahmed"
        )

    def test_parses_three_word_name(self):
        from zentinull.manifest.transforms import _employee_name_from_url

        assert (
            _employee_name_from_url(
                "https://moonliteconstruction.sharepoint.com/sites/MLIT-DEV/Agreements/Miguel%20Medina%20Aguirre__28/221328P_mmedina_SecPol_encrypted.pdf"
            )
            == "Miguel Medina Aguirre"
        )

    def test_unrelated_url_returns_empty(self):
        from zentinull.manifest.transforms import _employee_name_from_url

        assert _employee_name_from_url("https://example.com/some/other/path/foo.pdf") == ""

    def test_empty_input_returns_empty(self):
        from zentinull.manifest.transforms import _employee_name_from_url

        assert _employee_name_from_url("") == ""

    def test_registered_in_transform_registry(self):
        from zentinull.manifest.transforms import REGISTRY

        assert "employee_name_from_url" in REGISTRY
        assert callable(REGISTRY["employee_name_from_url"])
